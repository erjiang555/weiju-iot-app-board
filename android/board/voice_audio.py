"""Short, ephemeral PCM voice exchange for the paired mobile client."""
import audioop
import subprocess

SAMPLE_RATE = 16000
MAX_PLAY_BYTES = SAMPLE_RATE * 2 * 10
MAX_LISTEN_BYTES = SAMPLE_RATE * 2 * 12


def validate_pcm(data):
    if not 3200 <= len(data) <= MAX_PLAY_BYTES or len(data) % 2:
        raise ValueError('语音长度必须为 0.1 至 10 秒的 16 kHz 单声道 PCM')


def play_pcm(data):
    validate_pcm(data)
    for command in (
            ['amixer', 'sset', 'Speaker Playback ZC', 'on'],
            ['amixer', 'sset', 'Speaker', '115,115'],
            ['amixer', 'sset', 'Speaker AC', '4'],
            ['amixer', 'sset', 'Speaker DC', '4']):
        subprocess.check_call(command, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=4)
    raised = audioop.mul(data, 2, 3.0)
    resampled, _ = audioop.ratecv(raised, 2, 1, SAMPLE_RATE, 44100, None)
    stereo = audioop.tostereo(resampled, 2, 1, 1)
    process = subprocess.Popen(
        ['aplay', '-q', '-D', 'plughw:0,0', '-f', 'S16_LE',
         '-r', '44100', '-c', '2', '-t', 'raw'],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    try:
        process.communicate(stereo, timeout=16)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError('喇叭播放超时')
    if process.returncode:
        raise RuntimeError('喇叭播放失败，请检查音频设备')


def stream_microphone(send_headers, send_chunk):
    for command in (
            ['amixer', 'sset', 'Left Input Mixer Boost', 'on'],
            ['amixer', 'sset', 'Left Boost Mixer LINPUT1', 'on'],
            ['amixer', 'sset', 'Left Boost Mixer LINPUT2', 'on'],
            ['amixer', 'sset', 'Left Input Boost Mixer LINPUT1', '0'],
            ['amixer', 'sset', 'Left Input Boost Mixer LINPUT2', '5'],
            ['amixer', 'cset', 'name=Capture Volume', '50,50'],
            ['amixer', 'cset', 'name=ADC Data Output Select', '1']):
        subprocess.check_call(command, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=4)
    process = subprocess.Popen(
        ['arecord', '-q', '-D', 'plughw:0,0', '-f', 'S16_LE',
         '-r', '16000', '-c', '1', '-t', 'raw', '-d', '12'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0)
    try:
        first = process.stdout.read(4096)
        if not first:
            raise RuntimeError('板载麦克风无法录音')
        send_headers()
        send_chunk(first)
        remaining = MAX_LISTEN_BYTES - len(first)
        while remaining:
            chunk = process.stdout.read(min(4096, remaining))
            if not chunk:
                break
            send_chunk(chunk)
            remaining -= len(chunk)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
        process.stderr.close()
