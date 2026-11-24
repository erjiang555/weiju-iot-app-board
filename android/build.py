"""Build a signed Android APK with the installed SDK; no Gradle downloads."""
from pathlib import Path
import os
import secrets
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
SDK = Path(os.environ.get('ANDROID_SDK_ROOT', str(Path.home() / 'AppData/Local/Android/Sdk')))
JAVA = Path(os.environ.get('WEIJU_JDK', 'C:/Program Files/Microsoft/jdk-17.0.19.10-hotspot')) / 'bin'
TOOLS = SDK / 'build-tools/34.0.0'
ANDROID = SDK / 'platforms/android-36/android.jar'
BUILD = Path(tempfile.gettempdir()) / 'weiju-android-build'
DIST = ROOT / 'dist'
PRIVATE = ROOT / 'private'


def run(args):
    subprocess.run([str(a) for a in args], cwd=str(ROOT), check=True)


def main():
    for p in (BUILD / 'classes', BUILD / 'dex', DIST, PRIVATE):
        p.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT/'res', BUILD/'res', dirs_exist_ok=True)
    shutil.copytree(ROOT/'src', BUILD/'src', dirs_exist_ok=True)
    shutil.copy2(ROOT/'AndroidManifest.xml', BUILD/'AndroidManifest.xml')
    run([TOOLS/'aapt2.exe', 'compile', '--dir', BUILD/'res', '-o', BUILD/'resources.zip'])
    run([TOOLS/'aapt2.exe', 'link', '-o', BUILD/'resources.apk', '-I', ANDROID,
         '--manifest', BUILD/'AndroidManifest.xml', '--min-sdk-version', '26',
         '--target-sdk-version', '34', BUILD/'resources.zip'])
    sources = list((BUILD/'src').rglob('*.java'))
    run([JAVA/'javac.exe', '-encoding', 'UTF-8', '-source', '8', '-target', '8',
         '-Xlint:-options', '-classpath', ANDROID, '-d', BUILD/'classes'] + sources)
    run([JAVA/'java.exe', '-cp', TOOLS/'lib/d8.jar', 'com.android.tools.r8.D8',
         '--min-api', '26', '--lib', ANDROID, '--output', BUILD/'dex'] + list((BUILD/'classes').rglob('*.class')))
    shutil.copy2(BUILD/'resources.apk', BUILD/'unsigned.apk')
    with zipfile.ZipFile(BUILD/'unsigned.apk', 'a') as z:
        z.write(BUILD/'dex/classes.dex', 'classes.dex', compress_type=zipfile.ZIP_DEFLATED)
    run([TOOLS/'zipalign.exe', '-p', '-f', '4', BUILD/'unsigned.apk', BUILD/'aligned.apk'])
    password_file = PRIVATE/'signing-password.txt'
    key_file = PRIVATE/'release.jks'
    if not key_file.exists():
        if not password_file.exists():
            password_file.write_text(secrets.token_urlsafe(32), encoding='ascii')
        run([JAVA/'keytool.exe','-genkeypair','-keystore',key_file,'-storepass:file',password_file,
             '-keypass:file',password_file,'-alias','weiju','-keyalg','RSA','-keysize','3072',
             '-validity','10000','-dname','CN=Weiju IoT Personal Build, O=Weiju, C=CN'])
    apk = DIST/'Weiju-IoT-1.4.0.apk'
    run([JAVA/'java.exe','-jar',TOOLS/'lib/apksigner.jar','sign','--ks',key_file,
         '--ks-key-alias','weiju','--ks-pass','file:'+str(password_file),
         '--out',apk,BUILD/'aligned.apk'])
    run([JAVA/'java.exe','-jar',TOOLS/'lib/apksigner.jar','verify','--verbose',apk])
    shutil.copy2(apk,BUILD/'verified.apk')
    run([TOOLS/'aapt.exe','dump','badging',BUILD/'verified.apk'])
    print('APK_READY', apk, apk.stat().st_size)


if __name__ == '__main__':
    main()
