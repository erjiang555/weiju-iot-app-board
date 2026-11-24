package cn.weiju.iot;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.graphics.*;
import android.os.*;
import org.json.*;
import java.util.concurrent.*;
import java.text.SimpleDateFormat;
import java.util.*;

/** User-started foreground monitoring of the paired IoT device over LAN/VPN. */
public class AlertService extends Service {
    public static volatile boolean running=false;
    private static final String MONITOR="weiju-monitor",ALERTS="weiju-person-alerts";
    private ScheduledExecutorService executor;
    private NotificationManager manager;
    private boolean stopped=false;
    private int failures=0;

    @Override public void onCreate(){
        super.onCreate();manager=getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(MONITOR,"设备告警监听",NotificationManager.IMPORTANCE_LOW));
        NotificationChannel alerts=new NotificationChannel(ALERTS,"检测到有人",NotificationManager.IMPORTANCE_HIGH);
        alerts.setDescription("开发板抓拍并识别出人后提醒");alerts.enableVibration(true);manager.createNotificationChannel(alerts);
        executor=Executors.newSingleThreadScheduledExecutor();
    }
    private PendingIntent openApp(){
        Intent i=new Intent(this,MainActivity.class).putExtra("alerts",true).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(this,3,i,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    }
    private int icon(){return getResources().getIdentifier("ic_notification","drawable",getPackageName());}
    private Notification monitor(String message){
        Intent stop=new Intent(this,AlertService.class).setAction("STOP");
        return new Notification.Builder(this,MONITOR).setSmallIcon(icon()).setContentTitle("维聚物联 · 人体告警监听")
            .setContentText(message).setContentIntent(openApp()).setOngoing(true).setOnlyAlertOnce(true)
            .addAction(new Notification.Action.Builder(null,"停止监听",PendingIntent.getService(this,4,stop,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE)).build()).build();
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent!=null&&"STOP".equals(intent.getAction())){stopSelf();return START_NOT_STICKY;}
        if(!running){
            running=true;
            if(Build.VERSION.SDK_INT>=29)startForeground(100,monitor("正在连接开发板，请保持 VPN 在线"),ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
            else startForeground(100,monitor("正在连接开发板，请保持 VPN 在线"));
            executor.scheduleWithFixedDelay(this::check,0,3,TimeUnit.SECONDS);
        }
        return START_STICKY;
    }

    private void check(){
        if(stopped)return;
        try{
            DeviceClient client=DeviceClient.load(this);
            if(client==null)throw new Exception("请先导入设备配对文件");
            android.content.SharedPreferences prefs=getSharedPreferences("alerts",0);
            String key="cursor-"+client.pin;
            long cursor=prefs.getLong(key,-1);
            JSONObject result=client.request("/v1/alerts?after="+Math.max(0,cursor)+"&limit=100",null);
            JSONObject status=result.optJSONObject("status");
            if(status==null||!status.optBoolean("installed")||"unavailable".equals(status.optString("phase")))throw new Exception("开发板告警服务暂不可用");
            if(cursor<0){
                prefs.edit().putLong(key,result.optLong("latest_id",0)).commit();
            }else{
                JSONArray events=result.optJSONArray("events");
                if(events!=null)for(int i=0;i<events.length();i++){
                    JSONObject event=events.getJSONObject(i);long id=event.getLong("id");
                    if(id<=cursor)continue;
                    if(!"person_detected".equals(event.optString("type")))continue;
                    notifyEvent(client,event);cursor=id;prefs.edit().putLong(key,cursor).commit();
                }
            }
            failures=0;
            JSONObject sensor=status.optJSONObject("sensor");
            String message=!status.optBoolean("armed")?"自动布防已暂停，仍接收手动测试结果":sensor==null||!"ready".equals(sensor.optString("state"))?"已连接，传感器尚未完成校准":"监听中 · 确认有人后通知 · 每 3 秒同步";
            if(!stopped)manager.notify(100,monitor(message));
        }catch(Exception e){
            failures++;
            if(!stopped)manager.notify(100,monitor(failures<2?"连接重试中…":"暂时无法连接，请检查 Tailscale 和开发板"));
        }
    }

    private void notifyEvent(DeviceClient client,JSONObject e)throws Exception{
        if(stopped)return;
        long id=e.getLong("id"),at=e.optLong("created_at")*1000;
        String when=new SimpleDateFormat("HH:mm:ss",Locale.CHINA).format(new Date(at));
        String title="manual".equals(e.optString("trigger"))?"抓拍测试：识别到有人":"检测到有人活动";
        Notification.Builder b=new Notification.Builder(this,ALERTS).setSmallIcon(icon()).setContentTitle(title)
            .setContentText(when+" · 已保存现场截图，点击查看").setWhen(at).setShowWhen(true)
            .setAutoCancel(true).setContentIntent(openApp()).setCategory(Notification.CATEGORY_EVENT);
        try{
            byte[] raw=client.image("/v1/alerts/"+id+"/image");
            BitmapFactory.Options options=new BitmapFactory.Options();options.inSampleSize=2;
            Bitmap image=BitmapFactory.decodeByteArray(raw,0,raw.length,options);
            if(image!=null)b.setStyle(new Notification.BigPictureStyle().bigPicture(image).setSummaryText(when+" · 点击查看告警记录"));
        }catch(Exception ignored){}
        if(!stopped)manager.notify((int)(id%1000000000)+1000,b.build());
    }

    @Override public void onDestroy(){stopped=true;running=false;if(executor!=null)executor.shutdownNow();stopForeground(true);super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
