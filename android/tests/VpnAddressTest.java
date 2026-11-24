import cn.weiju.iot.DeviceClient;

/** Boundary tests for local/VPN endpoints; public hosts must remain rejected. */
public class VpnAddressTest {
    public static void main(String[] args) {
        String[] allowed={"192.168.1.100","10.0.0.1","172.16.0.1","172.31.255.254","100.64.0.1","100.127.255.254"};
        String[] denied={"100.63.255.255","100.128.0.1","8.8.8.8","172.32.0.1","192.169.1.1","127.0.0.1","100.64.0.256","100.064.0.1","100.64.0.1@evil.example","100.64.0.1/","::1"};
        for(String host:allowed)if(!DeviceClient.localAddress(host))throw new AssertionError("VPN/LAN endpoint rejected: "+host);
        for(String host:denied)if(DeviceClient.localAddress(host))throw new AssertionError("Invalid/public endpoint accepted: "+host);
        System.out.println("PASS VPN address range boundaries: "+(allowed.length+denied.length)+" cases");
    }
}
