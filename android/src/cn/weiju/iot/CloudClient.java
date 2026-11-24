package cn.weiju.iot;

import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import javax.net.ssl.HttpsURLConnection;

/** OneNET application API. Never logs credentials and never opens a device MQTT session. */
public final class CloudClient {
    private final DeviceClient pairing;
    private final String query;

    public CloudClient(DeviceClient p) throws Exception {
        pairing=p;
        query="?product_id="+URLEncoder.encode(p.productId,"UTF-8")+"&device_name="+URLEncoder.encode(p.name,"UTF-8");
    }

    private JSONObject call(String path, JSONObject body) throws Exception {
        if(!pairing.hasCloud())throw new IOException("请导入包含外网授权的新版配对文件");
        if(System.currentTimeMillis()/1000>=pairing.cloudExpires)throw new IOException("外网授权已到期，请导入新的配对文件");
        // Fixed vendor HTTPS origin, system trust and hostname checks, no redirects.
        HttpsURLConnection c=(HttpsURLConnection)new URL("https://iot-api.heclouds.com"+path).openConnection();
        c.setInstanceFollowRedirects(false);c.setConnectTimeout(7000);c.setReadTimeout(15000);c.setUseCaches(false);
        c.setRequestProperty("Authorization",pairing.cloudToken);
        try {
            if(body!=null){c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");byte[] bytes=body.toString().getBytes(StandardCharsets.UTF_8);c.setFixedLengthStreamingMode(bytes.length);try(OutputStream out=c.getOutputStream()){out.write(bytes);}}
            int http=c.getResponseCode();InputStream input=http==200?c.getInputStream():c.getErrorStream();
            if(input==null)throw new IOException("OneNET 暂不可用（HTTP "+http+"）");
            JSONObject response=new JSONObject(new String(DeviceClient.readLimited(input,131072),StandardCharsets.UTF_8));
            int code=response.optInt("code",-1);
            if(http!=200||code!=0){
                if(http==401||http==403)throw new IOException("外网授权无效或已过期，请更新配对文件");
                String message=response.optString("msg","");
                if(message.toLowerCase(Locale.ROOT).contains("offline"))throw new IOException("开发板已离线，请检查板子的网络和电源");
                throw new IOException("OneNET 请求失败（"+code+"）："+message);
            }
            return response;
        }finally{c.disconnect();}
    }

    public JSONObject status() throws Exception {
        JSONObject detail=call("/device/detail"+query,null).getJSONObject("data");
        JSONObject result=normalizeProperties(call("/thingmodel/query-device-property"+query,null),detail.optInt("status")==1);
        result.put("software_version","设备直连后查看").put("transport","cloud");
        return result;
    }

    public static JSONObject normalizeProperties(JSONObject response,boolean online) throws Exception {
        Object raw=response.opt("data");
        JSONArray list=raw instanceof JSONArray?(JSONArray)raw:raw instanceof JSONObject?((JSONObject)raw).optJSONArray("list"):null;
        if(list==null)throw new IOException("云端属性数据格式异常");
        JSONObject c=new JSONObject().put("phase",online?"online":"offline").put("device_online",online);
        long latest=0,tempTime=0,ledTime=0;
        for(int i=0;i<list.length();i++){
            JSONObject item=list.getJSONObject(i);String id=item.optString("identifier");
            long t=item.optLong("time",0);if(t>100000000000L)t/=1000;
            if("cpu_temperature".equals(id)){
                double n=Double.parseDouble(item.getString("value"));if(Double.isNaN(n)||Double.isInfinite(n)||n<-40||n>125)throw new IOException("云端温度数据无效");
                c.put(id,n);tempTime=t;latest=Math.max(latest,t);
            }else if("led_switch".equals(id)){
                String v=String.valueOf(item.get("value"));
                if(!"true".equals(v)&&!"false".equals(v))throw new IOException("云端 LED 状态无效");
                c.put(id,Boolean.parseBoolean(v));ledTime=t;latest=Math.max(latest,t);
            }
        }
        c.put("last_report_time",latest).put("temperature_time",tempTime).put("led_time",ledTime);
        return new JSONObject().put("control",c).put("sample_time",latest).put("api_version",1);
    }

    public JSONObject setLed(boolean value) throws Exception {
        long issued=System.currentTimeMillis()/1000;
        JSONObject body=new JSONObject().put("product_id",pairing.productId).put("device_name",pairing.name)
            .put("params",new JSONObject().put("led_switch",value));
        JSONObject ack=call("/thingmodel/set-device-property",body).optJSONObject("data");
        if(ack==null||ack.optInt("code",-1)!=200)throw new IOException("设备未确认执行成功，请刷新状态后再操作");
        // An accepted HTTP request is not hardware confirmation. Require the
        // device's 200 response and a new matching reported property.
        for(int n=0;n<6;n++){
            JSONObject state=normalizeProperties(call("/thingmodel/query-device-property"+query,null),true);
            JSONObject c=state.getJSONObject("control");
            if(c.has("led_switch")&&c.getBoolean("led_switch")==value&&c.optLong("led_time")>=issued-1)return state;
            Thread.sleep(500);
        }
        throw new IOException("命令已送达，状态尚未同步；请等待刷新，不要重复点击");
    }
}
