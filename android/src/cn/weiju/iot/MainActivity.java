package cn.weiju.iot;

import android.app.*;
import android.os.*;
import android.content.*;
import android.graphics.*;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.view.*;
import android.widget.*;
import org.json.JSONObject;
import org.json.JSONArray;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.*;
import javax.net.ssl.HttpsURLConnection;

public final class MainActivity extends Activity {
    private final int BG=Color.rgb(243,246,246), INK=Color.rgb(22,47,51), MUTED=Color.rgb(105,125,128), TEAL=Color.rgb(0,127,115), LINE=Color.rgb(221,231,229);
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newFixedThreadPool(3);
    private DeviceClient client;
    private LinearLayout root, body, nav;
    private TextView statusLine, temperature, cloud, ledValue, updateTime, version, cameraNote, errorLine;
    private Button ledOn, ledOff, cameraStart, cameraStop, cameraSave;
    private Button talkButton, listenButton;
    private TextView voiceNote;
    private volatile boolean recording=false, listening=false;
    private volatile boolean voiceBusy=false;
    private volatile int voiceGeneration=0;
    private volatile HttpsURLConnection voiceStream;
    private ImageView preview;
    private TrendView chart;
    private final ArrayList<Float> history = new ArrayList<>();
    private JSONObject lastStatus;
    private int page=0, generation=0;
    private boolean active=false, polling=false, commandBusy=false;
    private boolean cloudMode=false;
    private volatile boolean viewing=false;
    private volatile int videoGeneration=0;
    private volatile HttpsURLConnection stream;
    private volatile byte[] lastFrame;
    private long lastSuccess=0, historyTime=0;
    private LinearLayout alertList;
    private TextView alertStatus;
    private Button alertWatch;
    private boolean alertRefreshing=false;
    private long renderedAlertId=-1;
    private int renderedAlertCount=-1;
    private final android.util.LruCache<String,Bitmap> alertImages=new android.util.LruCache<>(20);
    private final Runnable ticker = new Runnable() { public void run() { if(active){ if(page==3)refreshAlerts();else poll();ui.postDelayed(this,page==3?2000:cloudMode?10000:3000); } } };

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        if(saved != null) page=saved.getInt("page",0);
        if(getIntent().getBooleanExtra("alerts",false))page=3;
        try { client=DeviceClient.load(this); } catch(Exception e) { toast("配对信息无法读取，请重新导入"); }
        cloudMode=client!=null&&client.hasCloud()&&getSharedPreferences("options",0).getBoolean("cloud",true);
        render();
    }
    @Override protected void onResume(){ super.onResume(); active=true; ui.removeCallbacks(ticker); ui.post(ticker); }
    @Override protected void onPause(){ active=false; ui.removeCallbacks(ticker); cancelVoice(); disconnectVideo(); super.onPause(); }
    @Override protected void onDestroy(){ cancelVoice(); disconnectVideo(); io.shutdownNow(); super.onDestroy(); }
    @Override protected void onSaveInstanceState(Bundle b){ b.putInt("page",page); super.onSaveInstanceState(b); }
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);if(intent.getBooleanExtra("alerts",false)){page=3;render();}}
    @Override public void onBackPressed(){ if(page!=0){ switchPage(0); } else super.onBackPressed(); }

    private int d(float n){ return Math.round(n*getResources().getDisplayMetrics().density); }
    private GradientDrawable shape(int color,int radius){ GradientDrawable x=new GradientDrawable();x.setColor(color);x.setCornerRadius(d(radius));return x; }
    private TextView text(String t,int size,int color,boolean bold){ TextView v=new TextView(this);v.setText(t);v.setTextSize(size);v.setTextColor(color);v.setIncludeFontPadding(false); if(bold)v.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));return v; }
    private void space(LinearLayout l,int h){ View v=new View(this);l.addView(v,new LinearLayout.LayoutParams(1,d(h))); }
    private LinearLayout vertical(){ LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.VERTICAL);return l; }
    private LinearLayout row(){ LinearLayout l=new LinearLayout(this);l.setGravity(Gravity.CENTER_VERTICAL);l.setOrientation(LinearLayout.HORIZONTAL);return l; }
    private LinearLayout card(){ LinearLayout c=vertical();c.setPadding(d(20),d(20),d(20),d(20));c.setBackground(shape(Color.WHITE,20));LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.bottomMargin=d(14);body.addView(c,p);return c; }
    private Button button(String title, boolean primary, View.OnClickListener action){
        Button b=new Button(this);b.setText(title);b.setTextSize(15);b.setAllCaps(false);b.setMinHeight(0);b.setMinimumHeight(0);b.setPadding(d(12),d(10),d(12),d(10));b.setTextColor(primary?Color.WHITE:TEAL);b.setBackground(shape(primary?TEAL:Color.rgb(230,245,241),12));b.setOnClickListener(action);return b;
    }
    private void fullButton(LinearLayout l,Button b){l.addView(b,new LinearLayout.LayoutParams(-1,d(48)));}
    private void pairButtons(LinearLayout l,Button a,Button b){ LinearLayout r=row();LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,d(48),1);p.rightMargin=d(8);r.addView(a,p);LinearLayout.LayoutParams q=new LinearLayout.LayoutParams(0,d(48),1);q.leftMargin=d(8);r.addView(b,q);l.addView(r);}
    private void label(LinearLayout l,String t){ l.addView(text(t,13,MUTED,false));space(l,8); }
    private void paragraph(LinearLayout l,String t){ TextView v=text(t,14,MUTED,false);v.setLineSpacing(d(5),1);l.addView(v); }
    private void toast(String t){ Toast.makeText(this,t,Toast.LENGTH_LONG).show(); }

    private void render(){
        temperature=cloud=ledValue=updateTime=version=cameraNote=errorLine=null;
        ledOn=ledOff=cameraStart=cameraStop=cameraSave=talkButton=listenButton=null;voiceNote=null;preview=null;chart=null;
        alertList=null;alertStatus=null;alertWatch=null;renderedAlertId=-1;renderedAlertCount=-1;
        root=vertical();root.setBackgroundColor(BG);root.setFitsSystemWindows(true);setContentView(root);
        LinearLayout header=vertical();header.setPadding(d(24),d(22),d(24),d(18));
        LinearLayout top=row();TextView title=text("维聚物联",27,INK,true);top.addView(title,new LinearLayout.LayoutParams(0,-2,1));
        TextView chip=text(cloudMode?"OneNET":client!=null&&client.isVpnAddress()?"VPN 直连":"局域网",11,TEAL,true);chip.setPadding(d(12),d(7),d(12),d(7));chip.setBackground(shape(Color.rgb(224,241,235),16));top.addView(chip);header.addView(top);space(header,9);
        statusLine=text(client==null?"让开发板，触手可及":"I.MX6ULL  ·  "+client.name,12,MUTED,false);header.addView(statusLine);root.addView(header);
        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(false);scroll.setClipToPadding(false);body=vertical();body.setPadding(d(20),d(2),d(20),d(16));scroll.addView(body);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        if(client==null && page!=2) onboarding();else if(page==0) dashboard();else if(page==1) cameraPage();else if(page==3) alertsPage();else settings();
        nav=row();nav.setPadding(d(12),d(8),d(12),d(8));nav.setBackgroundColor(Color.WHITE);
        String[] names={"◉  设备","▣  监控","◈  告警","⚙  设置"};int[] tabs={0,1,3,2};
        for(int i=0;i<4;i++){ final int n=tabs[i];TextView v=text(names[i],13,n==page?TEAL:MUTED,n==page);v.setGravity(Gravity.CENTER);v.setBackground(shape(n==page?Color.rgb(230,245,241):Color.WHITE,14));v.setOnClickListener(w->switchPage(n));nav.addView(v,new LinearLayout.LayoutParams(0,d(48),1)); }
        root.addView(nav);applyStatus();
    }

    private void onboarding(){
        LinearLayout hero=card();hero.setBackground(shape(INK,22));TextView dot=text("●",42,Color.rgb(126,227,194),true);hero.addView(dot);space(hero,18);hero.addView(text("你的设备\n随手掌控",32,Color.WHITE,true));space(hero,16);TextView desc=text("查看温度、控制灯光\n在手机上打开摄像头画面",15,Color.rgb(189,213,211),false);desc.setLineSpacing(d(6),1);hero.addView(desc);space(hero,24);
        fullButton(hero,button("导入设备配对文件",true,v->importPairing()));
        LinearLayout steps=card();label(steps,"首次连接");paragraph(steps,"1  将设备配对文件保存到手机\n2  点击上方按钮，选择配对文件\n3  选择 OneNET 或设备直连");space(steps,12);paragraph(steps,"外网控制支持手机流量和 Wi-Fi。摄像头支持同一 Wi-Fi，或连接 Tailscale VPN 后观看。");
    }

    private void dashboard(){
        LinearLayout modes=card();pairButtons(modes,button("外网 · OneNET",cloudMode,v->changeMode(true)),button("局域网 / VPN",!cloudMode,v->changeMode(false)));
        LinearLayout hero=card();hero.setBackground(shape(INK,22));labelDark(hero,"CPU 温度");
        temperature=text("— °C",52,Color.WHITE,true);hero.addView(temperature);space(hero,8);updateTime=text("正在连接开发板…",12,Color.rgb(180,208,204),false);hero.addView(updateTime);space(hero,14);chart=new TrendView();hero.addView(chart,new LinearLayout.LayoutParams(-1,d(64)));
        LinearLayout connection=card();label(connection,"连接状态");cloud=text("等待设备响应",18,INK,true);connection.addView(cloud);space(connection,8);errorLine=text("",13,MUTED,false);errorLine.setLineSpacing(d(3),1);connection.addView(errorLine);
        LinearLayout control=card();LinearLayout r=row();r.addView(text("板载 LED",19,INK,true),new LinearLayout.LayoutParams(0,-2,1));ledValue=text("—",14,MUTED,true);r.addView(ledValue);control.addView(r);space(control,8);paragraph(control,"与开发板屏幕和云端同步");space(control,18);ledOn=button("开灯",true,v->setLed(true));ledOff=button("关灯",false,v->setLed(false));pairButtons(control,ledOn,ledOff);
        LinearLayout video=card();video.addView(text("实时画面",19,INK,true));space(video,8);paragraph(video,"USB 彩色摄像头 · 640 × 480");space(video,16);fullButton(video,button("打开摄像头监控  →",false,v->switchPage(1)));
        LinearLayout about=card();label(about,"板端应用版本");version=text("—",20,INK,true);about.addView(version);space(about,7);paragraph(about,"已发布的云端升级由开发板自动检查和执行。版本详情通过设备直连查看。");
    }
    private void labelDark(LinearLayout l,String s){l.addView(text(s,14,Color.rgb(180,208,204),false));space(l,12);}

    private void cameraPage(){
        LinearLayout c=card();c.addView(text("摄像头监控",22,INK,true));space(c,8);paragraph(c,client!=null&&client.isVpnAddress()?"USB 彩色画面 · VPN 加密传输":"USB 彩色画面 · 局域网实时传输");space(c,18);
        preview=new ImageView(this);preview.setBackground(shape(INK,14));preview.setScaleType(ImageView.ScaleType.FIT_CENTER);preview.setContentDescription("开发板摄像头画面");c.addView(preview,new LinearLayout.LayoutParams(-1,d(225)));space(c,12);
        cameraNote=text("尚未开始监控",13,MUTED,false);cameraNote.setLineSpacing(d(3),1);c.addView(cameraNote);space(c,18);
        cameraStart=button("开始监控",true,v->startVideo());cameraStop=button("停止",false,v->stopVideo());pairButtons(c,cameraStart,cameraStop);space(c,12);cameraSave=button("保存当前画面",false,v->saveFrame());cameraSave.setEnabled(false);fullButton(c,cameraSave);
        LinearLayout voice=card();voice.addView(text("语音对讲",20,INK,true));space(voice,8);
        paragraph(voice,"按住说话：手机传到开发板喇叭。按住收听：开发板麦克风传到手机。");space(voice,16);
        talkButton=button("按住说话",true,v->{});listenButton=button("按住收听",false,v->{});
        pairButtons(voice,talkButton,listenButton);space(voice,12);
        voiceNote=text("等待操作",13,MUTED,false);voice.addView(voiceNote);
        talkButton.setOnTouchListener((v,e)->voiceTouch(e,true));
        listenButton.setOnTouchListener((v,e)->voiceTouch(e,false));
        space(voice,12);fullButton(voice,button("测试手机声音",false,v->testPhoneSpeaker()));
        LinearLayout tip=card();label(tip,"使用提示");paragraph(tip,"同一 Wi-Fi 可直接观看；外出时请先开启 Tailscale，并使用开发板的 VPN 地址。\n如果摄像头被占用，请先停止开发板屏幕上的预览。\n离开页面会断开观看，摄像头空闲约 20 秒后自动释放。");
    }

    private void settings(){
        LinearLayout c=card();c.addView(text("设备连接",22,INK,true));space(c,18);label(c,"设备地址");EditText address=new EditText(this);address.setText(client==null?"192.168.1.100":client.host);address.setSingleLine();address.setTextSize(17);address.setInputType(android.text.InputType.TYPE_CLASS_PHONE);address.setPadding(d(12),d(12),d(12),d(12));address.setBackground(shape(BG,10));c.addView(address,new LinearLayout.LayoutParams(-1,d(50)));space(c,12);paragraph(c,"同一 Wi-Fi 使用板子的局域网 IP。外出时使用 Tailscale 中以 100 开头的设备地址，并保持手机 VPN 已连接。");space(c,18);
        fullButton(c,button("保存地址并连接",true,v->{
            if(client==null){toast("请先导入设备配对文件");return;}
            try{DeviceClient candidate=new DeviceClient(client.toJson().put("host",address.getText().toString().trim()));candidate.save(this);useClient(candidate);toast("地址已保存");}catch(Exception e){toast(DeviceClient.message(e));}
        }));space(c,12);fullButton(c,button("导入设备配对文件",false,v->importPairing()));space(c,12);
        if(client!=null)fullButton(c,button("解除本机配对",false,v->new AlertDialog.Builder(this).setTitle("解除配对？").setMessage("只清除此手机保存的连接信息，开发板继续正常运行。").setNegativeButton("取消",null).setPositiveButton("解除",(a,b)->{getSharedPreferences("pairing",0).edit().clear().apply();useClient(null);}).show()));
        if(client!=null){LinearLayout auth=card();label(auth,"外网授权");paragraph(auth,client.hasCloud()?"有效至 "+new SimpleDateFormat("yyyy-MM-dd HH:mm",Locale.CHINA).format(new Date(client.cloudExpires*1000))+"\n到期后导入新配对文件即可续期。":"当前配对文件尚未包含外网授权，请导入新版文件。");}
        LinearLayout info=card();label(info,"维聚物联  1.4.0");paragraph(info,"适配 I.MX6ULL · Android 8.0 及以上\nOneNET：温度、设备状态、LED 控制。\n局域网或 VPN：监控、截图、人体告警、双向语音对讲。\nCSI 只触发抓拍，离线模型识别出人后才提醒。");space(info,14);paragraph(info,"配对文件包含产品的访问令牌，请只交给需要控制设备的人。连接信息保存在手机的加密存储中，产品密钥不进入 App。");
    }

    private void switchPage(int n){if(page==n)return;cancelVoice();disconnectVideo();page=n;render();poll();}
    private void useClient(DeviceClient c){cancelVoice();disconnectVideo();if(c==null)stopService(new Intent(this,AlertService.class));generation++;client=c;lastFrame=null;lastStatus=null;lastSuccess=0;historyTime=0;history.clear();cloudMode=c!=null&&c.hasCloud()&&!c.isVpnAddress();getSharedPreferences("options",0).edit().putBoolean("cloud",cloudMode).apply();page=0;render();poll();}
    private void changeMode(boolean external){if(external&&(client==null||!client.hasCloud())){toast("请先导入含外网授权的新版配对文件");return;}if(cloudMode==external)return;cloudMode=external;generation++;lastStatus=null;lastSuccess=0;historyTime=0;history.clear();getSharedPreferences("options",0).edit().putBoolean("cloud",external).apply();render();ui.removeCallbacks(ticker);ui.post(ticker);}
    private String networkMessage(Exception e,boolean external){if(external&&(e instanceof java.net.SocketTimeoutException||e instanceof java.net.ConnectException||e instanceof java.net.UnknownHostException))return "云端连接失败，请检查手机互联网连接后重试";return DeviceClient.message(e);}

    private void poll(){
        if(client==null||polling||!active)return;
        polling=true;final DeviceClient target=client;final int g=generation;final boolean external=cloudMode;
        io.execute(()->{try{
            JSONObject s=external?new CloudClient(target).status():target.request("/v1/status",null);
            ui.post(()->{if(g==generation){lastStatus=s;lastSuccess=System.currentTimeMillis();JSONObject x=s.optJSONObject("control");long sample=s.optLong("sample_time");if(x!=null && x.has("cpu_temperature")&&sample!=historyTime){historyTime=sample;history.add((float)x.optDouble("cpu_temperature"));if(history.size()>40)history.remove(0);}applyStatus();}});
        }catch(Exception e){String msg=networkMessage(e,external);ui.post(()->{if(g==generation){lastSuccess=0;applyStatus();if(errorLine!=null)errorLine.setText(msg);if(statusLine!=null&&page!=3)statusLine.setText(external?"云端连接中断 · 请检查网络":"连接中断 · 检查 Wi-Fi / VPN 和设备地址");}});}
        finally{ui.post(()->polling=false);}});
    }

    private void applyStatus(){
        boolean fresh=lastSuccess>0&&System.currentTimeMillis()-lastSuccess<(cloudMode?30000:10000);
        JSONObject c=lastStatus==null?null:lastStatus.optJSONObject("control");
        boolean ready=fresh&&c!=null;
        boolean online=ready&&"online".equals(c.optString("phase"))&&System.currentTimeMillis()/1000-c.optLong("last_report_time")<90;
        boolean controllable=ready&&(!cloudMode||online)&&c.has("led_switch");
        if(fresh&&statusLine!=null&&client!=null)statusLine.setText("I.MX6ULL  ·  "+client.name);
        if(temperature!=null)temperature.setText(ready&&c.has("cpu_temperature")?String.format(Locale.CHINA,"%.1f °C",c.optDouble("cpu_temperature")):"— °C");
        if(updateTime!=null){long at=ready&&cloudMode?c.optLong("temperature_time")*1000:lastSuccess;updateTime.setText(ready?(cloudMode?"最后上报 · ":"实时采样 · ")+new SimpleDateFormat(cloudMode?"MM-dd HH:mm:ss":"HH:mm:ss",Locale.CHINA).format(new Date(at)):"等待设备连接");}
        if(cloud!=null){cloud.setText(!fresh?(cloudMode?"云端未连接":"开发板未连接"):online?(cloudMode?"●  设备在线 · 外网控制":"●  设备在线 · 云端已同步"):cloudMode?"○  设备离线或数据已过期":"●  设备在线 · 云端未同步");cloud.setTextColor(fresh?TEAL:MUTED);}
        if(errorLine!=null)errorLine.setText(fresh?(ready?(cloudMode?(online?"通过 OneNET 连接 · 约 10 秒刷新":"显示最后上报的数据，控制暂不可用"):(client!=null&&client.isVpnAddress()?"手机通过 Tailscale VPN 连接开发板":"手机通过局域网连接开发板")):lastStatus.optString("control_error","控制服务不可用")):(cloudMode?"请确认手机能上网、开发板已连接 OneNET":"请确认同一 Wi-Fi 或 Tailscale VPN 已连接"));
        if(ledValue!=null)ledValue.setText(ready&&c.has("led_switch")?((cloudMode&&!online?"上次：":"")+(c.optBoolean("led_switch")?"● 已开启":"○ 已关闭")):"状态未知");
        if(ledOn!=null){ledOn.setEnabled(controllable&&!commandBusy);ledOff.setEnabled(controllable&&!commandBusy);ledOn.setAlpha(controllable?1f:0.4f);ledOff.setAlpha(controllable?1f:0.4f);}
        if(version!=null)version.setText(fresh?lastStatus.optString("software_version","未知"):"—");
        if(chart!=null)chart.invalidate();
        if(cameraStart!=null){cameraStart.setEnabled(client!=null&&!viewing);cameraStop.setEnabled(client!=null);cameraSave.setEnabled(lastFrame!=null);}
    }

    private void setLed(boolean value){
        if(client==null||commandBusy)return;commandBusy=true;applyStatus();final DeviceClient target=client;final int g=generation;final boolean external=cloudMode;
        io.execute(()->{try{JSONObject result=external?new CloudClient(target).setLed(value):target.request("/v1/led",new JSONObject().put("value",value));
            ui.post(()->{if(g==generation){try{if(lastStatus==null)lastStatus=new JSONObject();lastStatus.put("control",result.getJSONObject("control"));lastSuccess=System.currentTimeMillis();toast(value?"设备已开灯":"设备已关灯");}catch(Exception ignored){}}});
        }catch(Exception e){ui.post(()->{if(g==generation)toast(networkMessage(e,external));});}
        finally{ui.post(()->{commandBusy=false;applyStatus();poll();});}});
    }

    private boolean voiceTouch(MotionEvent event, boolean talk){
        switch(event.getActionMasked()){
            case MotionEvent.ACTION_DOWN:
                if(client==null){toast("请先连接开发板");return true;}
                if(voiceBusy){toast("请先结束当前语音操作");return true;}
                if(talk){
                    if(checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)!=android.content.pm.PackageManager.PERMISSION_GRANTED){
                        requestPermissions(new String[]{android.Manifest.permission.RECORD_AUDIO},21);
                        return true;
                    }
                    startTalking();
                }else{
                    AudioManager manager=(AudioManager)getSystemService(AUDIO_SERVICE);
                    if(manager!=null&&(manager.getStreamVolume(AudioManager.STREAM_MUSIC)==0||manager.isStreamMute(AudioManager.STREAM_MUSIC))){
                        voiceNote.setText("手机媒体音量为 0，请按音量键调高");
                        return true;
                    }
                    startListening();
                }
                return true;
            case MotionEvent.ACTION_UP:
                if(talk)recording=false;
                else stopListening();
                return true;
            case MotionEvent.ACTION_CANCEL:
                cancelVoice();
                return true;
            default:return true;
        }
    }

    private void voiceMessage(int g,String message){ui.post(()->{if(g==voiceGeneration&&voiceNote!=null)voiceNote.setText(message);});}

    private void startTalking(){
        final int g=++voiceGeneration;
        final DeviceClient target=client;
        voiceBusy=true;
        recording=true;
        voiceMessage(g,"正在录音… 松开后发送，最长 10 秒");
        io.execute(()->{
            AudioRecord recorder=null;
            ByteArrayOutputStream captured=new ByteArrayOutputStream();
            try{
                int min=AudioRecord.getMinBufferSize(16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT);
                if(min<=0)throw new IOException("手机麦克风不可用");
                recorder=new AudioRecord(MediaRecorder.AudioSource.MIC,16000,AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,Math.max(min,8192));
                if(recorder.getState()!=AudioRecord.STATE_INITIALIZED)throw new IOException("手机麦克风启动失败");
                if(!recording||g!=voiceGeneration)return;
                recorder.startRecording();
                byte[] buffer=new byte[4096];
                while(recording&&g==voiceGeneration&&captured.size()<320000){
                    int count=recorder.read(buffer,0,Math.min(buffer.length,320000-captured.size()));
                    if(count<0)throw new IOException("手机录音失败（"+count+"）");
                    if(count>0)captured.write(buffer,0,count);
                }
                if(g!=voiceGeneration)return;
                recording=false;
                if(captured.size()<3200){voiceMessage(g,"录音太短，请按住说话后再松开");return;}
                recorder.stop();
                recorder.release();
                recorder=null;
                voiceMessage(g,"正在发送到开发板…");
                byte[] pcm=captured.toByteArray();
                if((pcm.length&1)!=0)pcm=Arrays.copyOf(pcm,pcm.length-1);
                target.playVoice(pcm);
                voiceMessage(g,"语音已在开发板喇叭播放");
            }catch(Exception e){voiceMessage(g,DeviceClient.message(e));}
            finally{
                if(recorder!=null){
                    try{if(recorder.getRecordingState()==AudioRecord.RECORDSTATE_RECORDING)recorder.stop();}catch(IllegalStateException ignored){}
                    recorder.release();
                }
                if(g==voiceGeneration)recording=false;
                voiceBusy=false;
            }
        });
    }

    private void startListening(){
        final int g=++voiceGeneration;
        final DeviceClient target=client;
        voiceBusy=true;
        listening=true;
        voiceMessage(g,"正在连接板载麦克风…");
        io.execute(()->{
            HttpsURLConnection connection=null;
            AudioTrack player=null;
            try{
                connection=target.listenVoice();
                voiceStream=connection;
                if(!listening||g!=voiceGeneration)return;
                int min=AudioTrack.getMinBufferSize(16000,AudioFormat.CHANNEL_OUT_MONO,AudioFormat.ENCODING_PCM_16BIT);
                if(min<=0)throw new IOException("手机音频播放不可用");
                player=new AudioTrack(AudioManager.STREAM_MUSIC,16000,AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,Math.max(min,8192),AudioTrack.MODE_STREAM);
                if(player.getState()!=AudioTrack.STATE_INITIALIZED)throw new IOException("手机音频播放启动失败");
                player.play();
                voiceMessage(g,"正在收听开发板… 最长 12 秒");
                byte[] buffer=new byte[4097];
                InputStream input=connection.getInputStream();
                int count;
                int pending=0;
                int received=0;
                int peak=0;
                while(listening&&g==voiceGeneration&&(count=input.read(buffer,pending,4096))!=-1){
                    count+=pending;
                    pending=count&1;
                    count-=pending;
                    for(int i=0;i<count;i+=2){
                        int sample=(short)((buffer[i]&255)|((buffer[i+1]&255)<<8));
                        peak=Math.max(peak,Math.abs(sample));
                    }
                    int offset=0;
                    while(offset<count&&listening&&g==voiceGeneration){
                        int written=player.write(buffer,offset,count-offset);
                        if(written<=0)throw new IOException("手机音频播放中断");
                        offset+=written;
                    }
                    received+=count;
                    if(received%32768<count)voiceMessage(g,"正在收听… 收到 "+(received/1024)+" KB，播放 "+(player.getPlaybackHeadPosition()/16000)+" 秒");
                    if(pending!=0)buffer[0]=buffer[count];
                }
                if(g==voiceGeneration)voiceMessage(g,"收听已结束：收到 "+(received/1024)+" KB，播放 "+(player.getPlaybackHeadPosition()/16000)+" 秒，峰值 "+peak);
            }catch(Exception e){if(listening&&g==voiceGeneration)voiceMessage(g,DeviceClient.message(e));}
            finally{
                if(connection!=null)connection.disconnect();
                if(voiceStream==connection)voiceStream=null;
                if(player!=null){try{player.stop();}catch(IllegalStateException ignored){}player.release();}
                if(connection!=null&&(!listening||g!=voiceGeneration)){
                    try{target.request("/v1/voice/stop",new JSONObject());}catch(Exception ignored){}
                }
                if(g==voiceGeneration)listening=false;
                voiceBusy=false;
            }
        });
    }

    private void stopListening(){
        listening=false;
        HttpsURLConnection connection=voiceStream;
        if(connection!=null)connection.disconnect();
    }

    private void testPhoneSpeaker(){
        if(voiceBusy){toast("请先结束当前语音操作");return;}
        AudioManager manager=(AudioManager)getSystemService(AUDIO_SERVICE);
        if(manager!=null&&(manager.getStreamVolume(AudioManager.STREAM_MUSIC)==0||manager.isStreamMute(AudioManager.STREAM_MUSIC))){
            voiceNote.setText("手机媒体音量为 0，请按音量键调高");
            return;
        }
        final int g=++voiceGeneration;
        voiceBusy=true;
        voiceMessage(g,"正在测试手机扬声器…");
        io.execute(()->{
            AudioTrack player=null;
            try{
                int min=AudioTrack.getMinBufferSize(16000,AudioFormat.CHANNEL_OUT_MONO,AudioFormat.ENCODING_PCM_16BIT);
                if(min<=0)throw new IOException("手机音频播放不可用");
                player=new AudioTrack(AudioManager.STREAM_MUSIC,16000,AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,Math.max(min,8192),AudioTrack.MODE_STREAM);
                if(player.getState()!=AudioTrack.STATE_INITIALIZED)throw new IOException("手机音频播放启动失败");
                byte[] tone=new byte[16000];
                for(int i=0;i<8000;i++){
                    short sample=(short)(12000*Math.sin(2*Math.PI*550*i/16000));
                    tone[2*i]=(byte)sample;
                    tone[2*i+1]=(byte)(sample>>>8);
                }
                player.play();
                int offset=0;
                while(offset<tone.length){int written=player.write(tone,offset,tone.length-offset);if(written<=0)throw new IOException("手机音频播放中断");offset+=written;}
                Thread.sleep(750);
                voiceMessage(g,"测试音已播放");
            }catch(Exception e){voiceMessage(g,DeviceClient.message(e));}
            finally{if(player!=null){try{player.stop();}catch(IllegalStateException ignored){}player.release();}voiceBusy=false;}
        });
    }

    private void cancelVoice(){
        voiceGeneration++;
        recording=false;
        stopListening();
        if(voiceNote!=null)voiceNote.setText("语音已停止");
    }

    private void disconnectVideo(){viewing=false;videoGeneration++;HttpsURLConnection old=stream;stream=null;if(old!=null)io.execute(old::disconnect);if(cameraStart!=null){cameraStart.setEnabled(client!=null);if(cameraNote!=null)cameraNote.setText("监控已暂停，点击开始重新连接");}}
    private void stopVideo(){disconnectVideo();if(client==null)return;final DeviceClient target=client;io.execute(()->{try{target.request("/v1/camera/stop",new JSONObject());ui.post(()->{if(cameraNote!=null)cameraNote.setText("摄像头已停止");});}catch(Exception e){ui.post(()->toast(DeviceClient.message(e)));}});}
    private void startVideo(){
        if(client==null||viewing)return;viewing=true;final int vg=++videoGeneration;final DeviceClient target=client;lastFrame=null;applyStatus();cameraNote.setText("正在启动摄像头…");
        io.execute(()->{
            HttpsURLConnection conn=null;
            try{
                target.request("/v1/camera/start",new JSONObject());
                boolean ready=false;
                for(int n=0;n<16&&viewing&&vg==videoGeneration;n++){
                    JSONObject s=target.request("/v1/status",null).optJSONObject("camera");
                    if(s!=null&&"running".equals(s.optString("state"))){ready=true;break;}
                    if(s!=null&&"error".equals(s.optString("state")))throw new IOException(s.optString("error","摄像头启动失败"));
                    Thread.sleep(500);
                }
                if(!viewing||vg!=videoGeneration)return;
                if(!ready)throw new IOException("摄像头启动超时，请检查连接");
                conn=target.stream();stream=conn;
                BufferedInputStream input=new BufferedInputStream(conn.getInputStream(),65536);
                while(viewing&&vg==videoGeneration){
                    String line=readLine(input);if(line==null)throw new EOFException("画面已断开，请重新开始监控");
                    if(!line.startsWith("--weiju-frame"))continue;
                    int length=0;
                    while((line=readLine(input))!=null&&!line.isEmpty())if(line.toLowerCase(Locale.ROOT).startsWith("content-length:"))length=Integer.parseInt(line.substring(15).trim());
                    if(length<1||length>2097152)throw new IOException("无效的视频帧");
                    byte[] bytes=new byte[length];int off=0,n;while(off<length&&(n=input.read(bytes,off,length-off))>0)off+=n;
                    if(off!=length)throw new EOFException("画面传输中断");
                    Bitmap bitmap=BitmapFactory.decodeByteArray(bytes,0,length);if(bitmap==null)throw new IOException("画面解码失败");
                    if(viewing&&vg==videoGeneration){lastFrame=bytes;ui.post(()->{if(viewing&&vg==videoGeneration&&preview!=null){preview.setImageBitmap(bitmap);cameraNote.setText("●  实时画面  ·  640 × 480");cameraNote.setTextColor(TEAL);cameraSave.setEnabled(true);}});}
                }
            }catch(Exception e){String message=DeviceClient.message(e);ui.post(()->{if(vg==videoGeneration&&cameraNote!=null){cameraNote.setText(message);cameraNote.setTextColor(Color.rgb(168,75,48));}});}
            finally{if(conn!=null)conn.disconnect();if(vg==videoGeneration){stream=null;viewing=false;ui.post(this::applyStatus);}}
        });
    }
    private static String readLine(InputStream in)throws IOException{ByteArrayOutputStream out=new ByteArrayOutputStream();int b;while((b=in.read())!=-1){if(b=='\n')break;if(out.size()>2048)throw new IOException("视频头过长");if(b!='\r')out.write(b);}return b==-1&&out.size()==0?null:out.toString("US-ASCII");}

    private void importPairing(){Intent i=new Intent(Intent.ACTION_OPEN_DOCUMENT);i.addCategory(Intent.CATEGORY_OPENABLE);i.setType("*/*");startActivityForResult(i,10);}
    private void saveFrame(){if(lastFrame==null){toast("请先开始监控");return;}Intent i=new Intent(Intent.ACTION_CREATE_DOCUMENT);i.addCategory(Intent.CATEGORY_OPENABLE);i.setType("image/jpeg");i.putExtra(Intent.EXTRA_TITLE,"维聚摄像头_"+new SimpleDateFormat("yyyyMMdd_HHmmss",Locale.CHINA).format(new Date())+".jpg");startActivityForResult(i,11);}
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(result!=RESULT_OK||data==null||data.getData()==null)return;Uri uri=data.getData();
        if(request==10){io.execute(()->{try{byte[] raw=DeviceClient.readLimited(getContentResolver().openInputStream(uri),8192);DeviceClient candidate=new DeviceClient(new JSONObject(new String(raw,StandardCharsets.UTF_8)));ui.post(()->new AlertDialog.Builder(this).setTitle("连接这台开发板？").setMessage(candidate.name+"\n"+candidate.host+"\n导入后即可控制该设备。").setNegativeButton("取消",null).setPositiveButton("连接",(a,b)->{try{candidate.save(this);useClient(candidate);}catch(Exception e){toast(DeviceClient.message(e));}}).show());}catch(Exception e){ui.post(()->toast("无法导入："+DeviceClient.message(e)));}});}
        if(request==11){final byte[] frame=lastFrame;if(frame==null){toast("暂无画面可保存");return;}io.execute(()->{try(OutputStream out=getContentResolver().openOutputStream(uri)){if(out==null)throw new IOException("文件不可写");out.write(frame);ui.post(()->toast("画面已保存"));}catch(Exception e){ui.post(()->toast("保存失败："+DeviceClient.message(e)));}});}
    }

    private final class TrendView extends View{
        private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
        TrendView(){super(MainActivity.this);setContentDescription("本次连接期间的 CPU 温度趋势");}
        @Override protected void onDraw(Canvas canvas){super.onDraw(canvas);int w=getWidth(),h=getHeight();p.setStrokeWidth(d(1));p.setColor(Color.rgb(49,76,79));for(int i=1;i<4;i++)canvas.drawLine(0,h*i/4f,w,h*i/4f,p);if(history.size()<2)return;float min=Collections.min(history)-1,max=Collections.max(history)+1;Path path=new Path();for(int i=0;i<history.size();i++){float x=w*i/(float)(history.size()-1),y=h-d(6)-(history.get(i)-min)/(max-min)*(h-d(12));if(i==0)path.moveTo(x,y);else path.lineTo(x,y);}p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(d(2));p.setColor(Color.rgb(126,227,194));canvas.drawPath(path,p);p.setStyle(Paint.Style.FILL);}
    }

    private void alertsPage(){
        LinearLayout c=card();c.addView(text("人体活动告警",22,INK,true));space(c,9);paragraph(c,"Wi-Fi 感知活动 → 摄像头抓拍 → 识别人");space(c,16);
        alertStatus=text("正在读取开发板状态…",14,MUTED,false);alertStatus.setLineSpacing(d(5),1);c.addView(alertStatus);space(c,16);
        alertWatch=button(AlertService.running?"停止手机后台提醒":"开启手机后台提醒",true,v->toggleWatch());fullButton(c,alertWatch);space(c,12);
        pairButtons(c,button("开启布防",false,v->alertCommand("arm",true)),button("暂停布防",false,v->alertCommand("arm",false)));space(c,12);
        pairButtons(c,button("静态校准",false,v->new AlertDialog.Builder(this).setTitle("开始 30 秒校准？").setMessage("保持 ESP32 和路由器固定，检测区域内暂时不要走动。").setNegativeButton("取消",null).setPositiveButton("开始",(a,b)->alertCommand("calibrate",null)).show()),button("抓拍识别测试",false,v->alertCommand("capture",null)));space(c,12);
        paragraph(c,"后台提醒需要 Tailscale 在线，并允许通知和后台运行。系统强行停止 App 后，需重新打开监听。首次开启不会补发已有记录。");
        LinearLayout events=card();LinearLayout heading=row();heading.addView(text("最近告警",20,INK,true),new LinearLayout.LayoutParams(0,-2,1));Button refresh=button("刷新",false,v->{renderedAlertId=-1;refreshAlerts();});heading.addView(refresh,new LinearLayout.LayoutParams(d(76),d(40)));events.addView(heading);space(events,8);paragraph(events,"自动更新 · 点击图片可放大");space(events,14);alertList=vertical();events.addView(alertList);refreshAlerts();
    }
    private void toggleWatch(){
        if(AlertService.running){stopService(new Intent(this,AlertService.class));if(alertWatch!=null)alertWatch.setText("开启手机后台提醒");toast("手机后台提醒已停止");return;}
        if(Build.VERSION.SDK_INT>=33&&checkSelfPermission("android.permission.POST_NOTIFICATIONS")!=android.content.pm.PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"},20);return;}
        startForegroundService(new Intent(this,AlertService.class));if(alertWatch!=null)alertWatch.setText("停止手机后台提醒");toast("正在连接开发板并监听新告警");
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results){super.onRequestPermissionsResult(request,permissions,results);if(request==20){if(results.length>0&&results[0]==android.content.pm.PackageManager.PERMISSION_GRANTED)toggleWatch();else toast("通知未获允许，可在告警页手动查看记录");}else if(request==21){toast(results.length>0&&results[0]==android.content.pm.PackageManager.PERMISSION_GRANTED?"麦克风已允许，请按住说话":"麦克风未获允许，无法向开发板说话");}}
    private void alertCommand(String action,Boolean value){
        if(client==null)return;final DeviceClient target=client;
        io.execute(()->{try{JSONObject body=new JSONObject();if(value!=null)body.put("value",value);target.request("/v1/alerts/"+action,body);ui.post(()->{toast(action.equals("capture")?"已开始抓拍识别，约需 5–15 秒":action.equals("calibrate")?"已开始校准，请保持环境静止":"布防状态已更新");refreshAlerts();ui.postDelayed(this::refreshAlerts,15000);});}catch(Exception e){ui.post(()->toast(DeviceClient.message(e)));}});
    }
    private void refreshAlerts(){
        if(client==null||page!=3||alertList==null||alertRefreshing||isDestroyed())return;alertRefreshing=true;final DeviceClient target=client;final int g=generation;
        io.execute(()->{try{JSONObject result=target.request("/v1/alerts?after=0&limit=100",null);ui.post(()->{if(g==generation&&page==3&&alertList!=null)renderAlerts(result);});}catch(Exception e){ui.post(()->{if(g==generation&&page==3&&alertStatus!=null)alertStatus.setText(DeviceClient.message(e));});}finally{ui.post(()->alertRefreshing=false);}});
    }
    private void renderAlerts(JSONObject result){
        if(statusLine!=null&&client!=null)statusLine.setText("I.MX6ULL  ·  "+client.name);
        JSONObject s=result.optJSONObject("status"),sensor=s==null?null:s.optJSONObject("sensor");
        if(s==null||!s.optBoolean("installed"))alertStatus.setText("开发板告警服务尚未安装");
        else{
            String state=sensor==null?"":sensor.optString("state"),phase=s.optString("phase");
            String sensorText="ready".equals(state)?"传感器就绪":"calibrating".equals(state)?"正在校准 "+sensor.optInt("calibration_done")+"/"+sensor.optInt("calibration_total"):"calibration_failed".equals(state)?"校准失败，请固定设备并重新校准":"calibration_required".equals(state)?"需要静态校准":"needs_wifi".equals(state)?"ESP32 尚未连接 Wi-Fi":"等待 ESP32 数据";
            String work="capturing".equals(phase)?"正在抓拍":"recognizing".equals(phase)?"正在识别人":"unavailable".equals(phase)?"服务暂不可用":"error".equals(phase)?"错误："+s.optString("last_result"):"no_person".equals(s.optString("last_result"))?"上次抓拍未识别到人":"待命";
            alertStatus.setText((s.optBoolean("armed")?"● 已布防":"○ 布防已暂停")+" · "+sensorText+"\n"+work+"\n更新于 "+new SimpleDateFormat("HH:mm:ss",Locale.CHINA).format(new Date()));
        }
        if(alertWatch!=null)alertWatch.setText(AlertService.running?"停止手机后台提醒":"开启手机后台提醒");
        JSONArray a=result.optJSONArray("events");long latest=result.optLong("latest_id");int count=a==null?0:a.length();
        if(renderedAlertId==latest&&renderedAlertCount==count)return;
        renderedAlertId=latest;renderedAlertCount=count;alertList.removeAllViews();
        if(a==null||a.length()==0){paragraph(alertList,"暂无人体告警。只有识别到人的抓拍才会出现在这里。");return;}
        for(int i=a.length()-1;i>=Math.max(0,a.length()-20);i--){JSONObject event=a.optJSONObject(i);if(event==null)continue;long id=event.optLong("id");boolean hasCapture=event.has("captured_at");String when=new SimpleDateFormat("MM-dd HH:mm:ss",Locale.CHINA).format(new Date(event.optLong(hasCapture?"captured_at":"created_at")*1000));
            LinearLayout item=vertical();item.setPadding(d(12),d(12),d(12),d(12));item.setBackground(shape(BG,12));item.setOnClickListener(v->showAlertImage(event));
            item.addView(text((hasCapture?"抓拍 ":"告警 ")+when,14,INK,true));space(item,6);
            item.addView(text(event.optInt("person_count")+" 人 · "+("manual".equals(event.optString("trigger"))?"手动测试":"Wi-Fi 活动触发"),12,MUTED,false));space(item,10);
            ImageView thumbnail=new ImageView(this);thumbnail.setBackground(shape(INK,8));thumbnail.setScaleType(ImageView.ScaleType.FIT_CENTER);thumbnail.setContentDescription("告警截图，点击放大");thumbnail.setOnClickListener(v->showAlertImage(event));item.addView(thumbnail,new LinearLayout.LayoutParams(-1,d(160)));
            TextView hint=text("正在加载截图…",12,MUTED,false);space(item,6);item.addView(hint);alertList.addView(item);space(alertList,14);
            loadAlertThumbnail(event,thumbnail,hint);
        }
    }
    private void loadAlertThumbnail(JSONObject event,ImageView targetView,TextView hint){
        if(client==null)return;final DeviceClient target=client;final int g=generation;long id=event.optLong("id");String key=client.pin+":"+id;Bitmap cached=alertImages.get(key);
        if(cached!=null){targetView.setImageBitmap(cached);hint.setText("点击查看大图");return;}
        io.execute(()->{try{byte[] bytes=target.image("/v1/alerts/"+id+"/image");BitmapFactory.Options options=new BitmapFactory.Options();options.inSampleSize=2;Bitmap bitmap=BitmapFactory.decodeByteArray(bytes,0,bytes.length,options);if(bitmap==null)throw new IOException("截图无法解码");alertImages.put(key,bitmap);ui.post(()->{if(g==generation&&!isDestroyed()){targetView.setImageBitmap(bitmap);hint.setText("点击查看大图");}});}catch(Exception e){ui.post(()->{if(g==generation&&!isDestroyed())hint.setText("截图加载失败，点击记录重试");});}});
    }
    private void showAlertImage(JSONObject event){
        if(client==null)return;final DeviceClient target=client;long id=event.optLong("id");toast("正在加载抓拍…");
        io.execute(()->{try{byte[] bytes=target.image("/v1/alerts/"+id+"/image");Bitmap b=BitmapFactory.decodeByteArray(bytes,0,bytes.length);if(b==null)throw new IOException("无法解码截图");ui.post(()->{ImageView image=new ImageView(this);image.setAdjustViewBounds(true);image.setImageBitmap(b);image.setPadding(d(12),d(12),d(12),d(12));new AlertDialog.Builder(this).setTitle("识别到 "+event.optInt("person_count")+" 人").setView(image).setPositiveButton("关闭",null).show();});}catch(Exception e){ui.post(()->toast(DeviceClient.message(e)));}});
    }
}
