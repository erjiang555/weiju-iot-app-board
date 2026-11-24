package cn.weiju.iot;

import android.content.Context;
import android.net.Uri;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.X509Certificate;
import java.security.cert.CertificateException;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
import javax.net.ssl.*;

public final class DeviceClient {
    public final String host, token, pin, name;
    public final String cloudToken, productId;
    public final long cloudExpires;
    private final SSLSocketFactory sockets;

    public DeviceClient(JSONObject data) throws Exception {
        host = data.getString("host").trim();
        token = data.getString("token");
        pin = data.getString("cert_sha256").toLowerCase(java.util.Locale.ROOT);
        name = data.optString("device_name", "imx6ull_001");
        cloudToken = data.optString("cloud_token", "");
        productId = data.optString("product_id", "");
        long expiry = data.optLong("cloud_expires", 0);
        if (!cloudToken.isEmpty()) {
            Uri parsed=Uri.parse("https://pairing.invalid/?"+cloudToken);
            String expires=parsed.getQueryParameter("et");
            if(!"2022-05-01".equals(parsed.getQueryParameter("version")) ||
                !("products/"+productId).equals(parsed.getQueryParameter("res")) ||
                expires==null || !expires.matches("[0-9]{10}") || cloudToken.contains("\r") || cloudToken.contains("\n"))
                throw new IllegalArgumentException("云端配对信息无效");
            expiry=Long.parseLong(expires);
        }
        cloudExpires=expiry;
        if (!localAddress(host) || !token.matches("[A-Za-z0-9_-]{32,128}") || !pin.matches("[a-f0-9]{64}"))
            throw new IllegalArgumentException("配对文件无效，请选择随安装包提供的设备配对文件");
        SSLContext ssl = SSLContext.getInstance("TLS");
        ssl.init(null, new TrustManager[]{new X509TrustManager() {
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
            public void checkClientTrusted(X509Certificate[] c, String a) throws CertificateException { throw new CertificateException(); }
            public void checkServerTrusted(X509Certificate[] chain, String a) throws CertificateException {
                try {
                    if (chain == null || chain.length == 0) throw new CertificateException("缺少设备证书");
                    chain[0].checkValidity();
                    String actual = hex(MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded()));
                    if (!MessageDigest.isEqual(pin.getBytes(StandardCharsets.US_ASCII), actual.getBytes(StandardCharsets.US_ASCII)))
                        throw new CertificateException("设备身份不匹配，请核对配对文件");
                } catch (CertificateException e) { throw e; }
                catch (Exception e) { throw new CertificateException(e); }
            }
        }}, new SecureRandom());
        sockets = ssl.getSocketFactory();
    }

    public static boolean localAddress(String s) {
        String[] p = s.split("\\.", -1);
        if (p.length != 4) return false;
        int[] n = new int[4];
        for (int i = 0; i < 4; i++) {
            if (!p[i].matches("0|[1-9][0-9]{0,2}")) return false;
            n[i] = Integer.parseInt(p[i]);
            if (n[i] > 255) return false;
        }
        return n[0] == 10 || (n[0] == 192 && n[1] == 168) || (n[0] == 172 && n[1] >= 16 && n[1] <= 31)
            || (n[0] == 100 && n[1] >= 64 && n[1] <= 127); // Tailscale private overlay; TLS pin still required.
    }

    public boolean isVpnAddress() { return host.startsWith("100."); }

    private HttpsURLConnection open(String path) throws Exception {
        if (!path.startsWith("/v1/") || path.contains("..")) throw new IOException("无效接口");
        HttpsURLConnection c = (HttpsURLConnection)new URL("https://" + host + ":8443" + path).openConnection();
        c.setSSLSocketFactory(sockets);
        // The exact paired leaf certificate is the identity, independent of DHCP IP.
        c.setHostnameVerifier((hostname, session) -> {
            try {
                return hostname.equals(host) && pin.equals(hex(MessageDigest.getInstance("SHA-256")
                    .digest(session.getPeerCertificates()[0].getEncoded())));
            } catch (Exception e) { return false; }
        });
        c.setInstanceFollowRedirects(false);
        c.setConnectTimeout(4500);
        c.setReadTimeout(6000);
        c.setUseCaches(false);
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setRequestProperty("Connection", "close");
        return c;
    }

    public JSONObject request(String path, JSONObject body) throws Exception {
        HttpsURLConnection c = open(path);
        try {
            if (body != null) {
                c.setRequestMethod("POST"); c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                c.setFixedLengthStreamingMode(bytes.length);
                try (OutputStream out = c.getOutputStream()) { out.write(bytes); }
            }
            int status = c.getResponseCode();
            InputStream in = status == 200 ? c.getInputStream() : c.getErrorStream();
            JSONObject result = in == null ? new JSONObject() : new JSONObject(new String(readLimited(in, 65536), StandardCharsets.UTF_8));
            if (status != 200) throw new IOException(result.optString("error", "设备返回错误 " + status));
            return result;
        } finally { c.disconnect(); }
    }

    public HttpsURLConnection stream() throws Exception {
        HttpsURLConnection c = open("/v1/camera/stream");
        try {
            int status = c.getResponseCode();
            if (status != 200) throw new IOException("摄像头尚未就绪（" + status + "）");
            return c;
        } catch (Exception e) { c.disconnect(); throw e; }
    }

    public void playVoice(byte[] pcm) throws Exception {
        if (pcm == null || pcm.length < 3200 || pcm.length > 320000 || (pcm.length & 1) != 0)
            throw new IOException("语音长度必须为 0.1 至 10 秒");
        HttpsURLConnection c = open("/v1/voice/play");
        c.setReadTimeout(22000);
        try {
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/vnd.weiju.pcm16le");
            c.setFixedLengthStreamingMode(pcm.length);
            try (OutputStream out = c.getOutputStream()) { out.write(pcm); }
            int status = c.getResponseCode();
            if (status != 200) {
                InputStream error = c.getErrorStream();
                if (error != null) {
                    JSONObject result = new JSONObject(new String(readLimited(error, 4096), StandardCharsets.UTF_8));
                    throw new IOException(result.optString("error", "语音发送失败"));
                }
                throw new IOException("语音发送失败（" + status + "）");
            }
        } finally { c.disconnect(); }
    }

    public HttpsURLConnection listenVoice() throws Exception {
        HttpsURLConnection c = open("/v1/voice/listen");
        c.setReadTimeout(6000);
        try {
            int status = c.getResponseCode();
            if (status != 200) {
                InputStream error = c.getErrorStream();
                if (error != null) {
                    JSONObject result = new JSONObject(new String(readLimited(error, 4096), StandardCharsets.UTF_8));
                    throw new IOException(result.optString("error", "收听失败"));
                }
                throw new IOException("收听失败（" + status + "）");
            }
            if (!"application/vnd.weiju.pcm16le".equals(c.getContentType()))
                throw new IOException("板端语音格式异常");
            return c;
        } catch (Exception e) { c.disconnect(); throw e; }
    }

    public byte[] image(String path) throws Exception {
        if(!path.matches("/v1/alerts/[0-9]{1,18}/image"))throw new IOException("无效的截图地址");
        try{return fetchImage(path);}
        catch(SSLException e){throw e;}
        catch(IOException e){Thread.sleep(300);return fetchImage(path);}
    }

    private byte[] fetchImage(String path) throws Exception {
        HttpsURLConnection c=open(path);
        c.setReadTimeout(10000);
        try{
            if(c.getResponseCode()!=200)throw new IOException("截图不存在或已过期");
            if(!"image/jpeg".equals(c.getContentType()))throw new IOException("截图格式异常");
            return readLimited(c.getInputStream(),1048576);
        }finally{c.disconnect();}
    }

    public JSONObject toJson() throws Exception {
        return new JSONObject().put("host",host).put("token",token).put("cert_sha256",pin).put("device_name",name)
            .put("cloud_token",cloudToken).put("cloud_expires",cloudExpires).put("product_id",productId);
    }

    public boolean hasCloud() { return !cloudToken.isEmpty() && cloudExpires > 0; }

    private static SecretKey storageKey() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore"); ks.load(null);
        String alias = "weiju-pairing-v1";
        if (ks.containsAlias(alias)) return (SecretKey)ks.getKey(alias, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }

    public void save(Context context) throws Exception {
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding"); c.init(Cipher.ENCRYPT_MODE, storageKey());
        String iv = Base64.encodeToString(c.getIV(), Base64.NO_WRAP);
        String data = Base64.encodeToString(c.doFinal(toJson().toString().getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
        if (!context.getSharedPreferences("pairing",0).edit().putString("iv",iv).putString("data",data).commit())
            throw new IOException("无法保存配对信息");
    }

    public static DeviceClient load(Context context) throws Exception {
        android.content.SharedPreferences p = context.getSharedPreferences("pairing",0);
        if (!p.contains("data")) return null;
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.DECRYPT_MODE, storageKey(), new GCMParameterSpec(128, Base64.decode(p.getString("iv",""),Base64.NO_WRAP)));
        return new DeviceClient(new JSONObject(new String(c.doFinal(Base64.decode(p.getString("data",""),Base64.NO_WRAP)),StandardCharsets.UTF_8)));
    }

    public static byte[] readLimited(InputStream in, int max) throws IOException {
        try (InputStream source = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buf = new byte[8192]; int n;
            while ((n = source.read(buf)) != -1) {
                if (out.size() + n > max) throw new IOException("返回内容过大");
                out.write(buf,0,n);
            }
            return out.toByteArray();
        }
    }

    public static String hex(byte[] bytes) {
        StringBuilder s = new StringBuilder(); for (byte b : bytes) s.append(String.format(java.util.Locale.ROOT,"%02x",b & 255)); return s.toString();
    }

    public static String message(Exception e) {
        if (e instanceof SSLException || e instanceof CertificateException) return "设备证书校验失败，请检查手机日期和配对文件";
        if (e instanceof SocketTimeoutException || e instanceof ConnectException || e instanceof NoRouteToHostException)
            return "连接不到开发板，请检查同一 Wi-Fi 或 Tailscale VPN，以及设备 IP";
        return e.getMessage() == null ? "连接失败，请重试" : e.getMessage();
    }
}
