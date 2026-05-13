# Day 6 - RAG smoke test (Filip grades)

Corpus: knowledge_base/docs/isr1100-sw-config.pdf (1 of 7; ~8.8 MB / 692 chunks).
Embedding model: sentence-transformers/all-MiniLM-L6-v2.
Distance metric: cosine (score = 1 - distance, higher = more relevant).
Method: each query runs through search_docs(top_k=3). Filip scores the TOP result:
  - 1 if relevant (chunk text answers, or clearly leads to the answer)
  - 0 if not relevant (off-topic, generic, or wrong section)

**Target: >= 7 / 10.**

## Q1. How do I change the hostname on a Cisco ISR 1100?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Configuring Global Parameters] (score 0.495)

> Router(config)# telnet router-name or address Login: login-id Password: ********* Router> enable Specifies the name for the router.hostname name Example: Step 2 Router(config)# hostname Router Specifies a password to prevent unauthorized access to the router. enable password password Example: Step 3...

**#2** [isr1100-sw-config.pdf - 1000 Series ISR to successfully resolve the FQDN.] (score 0.459)

> ISR as a Pass-through Server You can identify the traffic to be bypassed using domain names. In the Cisco 1000 Series ISR, you can define these domains in the form of regular expressions. If the DNS query that is intercepted by the Cisco 1000 Series ISR matches one of the configured regular expressi...

**#3** [isr1100-sw-config.pdf - Configuring Cisco Embedded Wireless Controller (EWC)] (score 0.445)

> Server > Add new Pool . The Add DHCP Pool window will pop up. Step 1 On the Add DHCP Pool window. Enter the following fields: Step 2 • Enter the Pool Name for the WLAN • Enable the Pool Status • Enter the VLAN ID for the WLAN • Enter the Lease Period for the DHCP clients. Default is 1 Day • Enter th...

## Q2. Default management interface on the C1111 - name and IP?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Understanding Diagnostic Mode] (score 0.474)

> enable name-caching no Negate a command or set its defaults nrzi-encoding Enable use of NRZI encoding ntp Configure NTP . . . Router(config-if)# Cisco 1000 Series Integrated Services Router Software Configuration Guide 12 Using Cisco IOS XE Software Finding Command Options: Example CommentCommand En...

**#2** [isr1100-sw-config.pdf - 255.255.255.0 IP subnet mask.] (score 0.461)

> command without the no keyword to re-enable a disabled function or to enable a function that is disabled by default. For example, IP routing is enabled by default. To disable IP routing, use the no ip routing command; to re-enable IP routing, use the ip routing command. The Cisco IOS software comman...

**#3** [isr1100-sw-config.pdf - Configuring Wi-Fi 6] (score 0.444)

> describes how to configure the WiFi card to the internal switch interface on the Cisco C1100 Integrated Services Routers (ISRs). The WiFi card is connected to the internal switch interface, the Wlan-GigabitEthernet interface. The configuration of this interface is identical to the GigabitEthernet 0/...

## Q3. How do I enable the WebUI on a Cisco router (ip http server)?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Configuring Console Port for Modem Connection] (score 0.722)

> is active and functions properly. Then, connect the analog phone line to the modem. Step 6 Initialize an EXEC modem call to the router from another device (PC) to test the modem connection. Step 7 When the connection is established, the dial in client is prompted for a password. Enter the correct pa...

**#2** [isr1100-sw-config.pdf - CHAPTER 11] (score 0.698)

> switch port which is the member of VLAN1. By default, all the ports will be the member of VLAN1 and the PC recieves the IP address from the pool WEBUIPool. Step 4 After your PC receives the IP address, launch the browser, type https://192.168.1.1/webui/#/dayZeroRouting or enter http://192.168.1.1/we...

**#3** [isr1100-sw-config.pdf - CHAPTER 11] (score 0.693)

> errors. • You need a user with privilege 15 to access the configuration screens on Web UI. If the privilege is less than 15, you can access only the Dashboard and Monitoring screens on Web UI. To create a user account,use the username <username>privilege <privilege>password 0 <passwordtext> Device #...

## Q4. What interfaces does the C1111 expose? Naming convention?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Configuring Wi-Fi 6] (score 0.455)

> describes how to configure the WiFi card to the internal switch interface on the Cisco C1100 Integrated Services Routers (ISRs). The WiFi card is connected to the internal switch interface, the Wlan-GigabitEthernet interface. The configuration of this interface is identical to the GigabitEthernet 0/...

**#2** [isr1100-sw-config.pdf - CHAPTER 31] (score 0.396)

> regardless of their type and application. Slot and Subslots for WLAN This section contains information on slots and subslots for WLAN. Slots specify the chassis slot number in your router and subslots specify the slot where the service modules are installed. The table below describes the slot number...

**#3** [isr1100-sw-config.pdf - Configuring Bridge Domain Interfaces] (score 0.391)

> • Both QinQ (inner and outer) VLAN tags, or both 802.1ad S-VLAN and C-VLAN tags Cisco 1000 Series Integrated Services Router Software Configuration Guide 260 Information About Bridge Domain Interface • Outer 802.1p CoS bits, inner 802.1p CoS bits, or both • Payload Ethernet type (five choices are su...

## Q5. How do I configure a management IP and default gateway on a C1111?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Configuring Routing Information Protocol] (score 0.451)

> IS-IS summary, L1 - IS-IS level-1, L2 - IS-IS level-2 ia - IS-IS inter area, * - candidate default, U - per-user static route o - ODR, P - periodic downloaded static route Gateway of last resort is not set 10.0.0.0/24 is subnetted, 1 subnets C 10.108.1.0 is directly connected, Loopback0 R 192.0.2.2/...

**#2** [isr1100-sw-config.pdf - Managing Configuration Files] (score 0.442)

> software versions, and some boot images. Enter enable password: ******** The virtual terminal password is used to protect access to the router over a network interface. Enter virtual terminal password:******** Cisco 1000 Series Integrated Services Router Software Configuration Guide 18 Using Cisco I...

**#3** [isr1100-sw-config.pdf - Understanding Diagnostic Mode] (score 0.425)

> enable name-caching no Negate a command or set its defaults nrzi-encoding Enable use of NRZI encoding ntp Configure NTP . . . Router(config-if)# Cisco 1000 Series Integrated Services Router Software Configuration Guide 12 Using Cisco IOS XE Software Finding Command Options: Example CommentCommand En...

## Q6. Difference between switchport and routed mode on C1111 ports?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Configuring Flex Port to Layer 2 Port] (score 0.498)

> device. interface type number Example: Step 3 Device(config-if)# interface GigabitEthernet 0/1/6 Converts the port from Layer 3 interface to Layer 2 interface and makes it a routing interface rather than a switch port. switchport Example: Device(config-if)# switchport Step 4 Configures the operation...

**#2** [isr1100-sw-config.pdf - Configuring Wi-Fi 6] (score 0.451)

> describes how to configure the WiFi card to the internal switch interface on the Cisco C1100 Integrated Services Routers (ISRs). The WiFi card is connected to the internal switch interface, the Wlan-GigabitEthernet interface. The configuration of this interface is identical to the GigabitEthernet 0/...

**#3** [isr1100-sw-config.pdf - Configuring LACP] (score 0.396)

> Cisco 1000 Series Integrated Services Routers. Alternatively, you can check L3 port channel on L3 physical interface. From Cisco IOS XE Dublin 17.11.x release, up to 2 switchports can be configured on the L3 interface for the entire Cisco 1000 Series Integrated Services Routers. For more information...

## Q7. How is the C1111 management VLAN configured?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Configuring Wi-Fi 6] (score 0.552)

> describes how to configure the WiFi card to the internal switch interface on the Cisco C1100 Integrated Services Routers (ISRs). The WiFi card is connected to the internal switch interface, the Wlan-GigabitEthernet interface. The configuration of this interface is identical to the GigabitEthernet 0/...

**#2** [isr1100-sw-config.pdf - 0 C1111-8PLTELAWN ok 00:04:56] (score 0.465)

> 0/2 C1111-LTE ok 00:02:41 0/3 ISR-AP1100AC-N ok 00:02:41 R0 C1111-8PLTELAWN ok, active 00:04:56 F0 C1111-8PLTELAWN ok, active 00:04:56 P0 PWR-12V ok 00:04:30 Slot CPLD Version Firmware Version --------- ------------------- --------------------------------------- 0 17100501 16.6(1r)RC3 R0 17100501 16...

**#3** [isr1100-sw-config.pdf - CHAPTER 31] (score 0.459)

> controller, on page 465 • Using internal DHCP server on Cisco Mobility Express , on page 475 • Configuring Cisco Mobility Express for Site Survey, on page 477 • Creating Wireless Networks , on page 481 • Managing Services with Cisco Mobility Express , on page 490 • Managing the Cisco Mobility Expres...

## Q8. Boot and initialization lifecycle of the ISR 1100 series?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Configuring ROMMON] (score 0.474)

> on page 113 ROMmon Images A ROMmon image is a software package used by ROM Monitor (ROMmon) software on a router. The software package is separate from the consolidated package normally used to boot the router. For more information on ROMmon, see the "ROM Monitor Overview and Basic Procedures" secti...

**#2** [isr1100-sw-config.pdf - 0 C1111-8PLTELAWN ok 00:04:56] (score 0.467)

> image for the first time, the device checks the installed version of the ROMMON, and upgrades if the system is running an older version. During the upgrade, do not power cycle the device. The system automatically power cycles the device after the new ROMMON is installed. After the installation, the ...

**#3** [isr1100-sw-config.pdf - Configuring Wi-Fi 6] (score 0.461)

> ISR-AP1100AX-B • ISR-AP1100AX-E • ISR-AP1100AX-Q • ISR-AP1100AX-Z Router#show platform Chassis type: C1131X-8PLTEPWB Slot Type State Insert time (ago) --------- ------------------- --------------------- ----------------- 0/0 C1131X-2x1GE ok 3w2d Cisco 1000 Series Integrated Services Router Software ...

## Q9. Where do I configure VLANs on the C1111 in the CLI?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - Configuring Wi-Fi 6] (score 0.496)

> describes how to configure the WiFi card to the internal switch interface on the Cisco C1100 Integrated Services Routers (ISRs). The WiFi card is connected to the internal switch interface, the Wlan-GigabitEthernet interface. The configuration of this interface is identical to the GigabitEthernet 0/...

**#2** [isr1100-sw-config.pdf - Configuring Wi-Fi 6] (score 0.446)

> interface Wlan-GigabitEthernet slot/subslot/port Example: Step 8 Router(config)#interface Wlan-GigabitEthernet 0/1/8 Use the switchport access vlan command to assign the port or range of ports into access ports. switchport accessvlan number Example: Router(config-if)#switchportaccess vlan 199 Step 9...

**#3** [isr1100-sw-config.pdf - CHAPTER 9] (score 0.431)

> IPv4 address glean (using security-levelglean) and tracking (using tracking enable). • Multi-auth per user VLAN assignment is not supported. • NEAT/CISP is not supported. How to Configure Change of Authorization Essential dot1x | SANet Configuration aaa new-model aaa authentication dot1x default gro...

## Q10. How do I save running-config persistently on a Cisco router?

Score: __ / 1

**#1** [isr1100-sw-config.pdf - 255.255.255.0 IP subnet mask.] (score 0.630)

> requested by Exec. Reload Reason: Factory Reset. ***Return to ROMMON Prompt Saving Configuration Changes Use the copy running-config startup-config command to save your configuration changes to the startup configuration so that the changes will not be lost if the software reloads or a power outage o...

**#2** [isr1100-sw-config.pdf - CHAPTER 8] (score 0.492)

> database file on a secure server. When the switch is returned to the default system configuration, you can download the saved files to the switch by using the Xmodem protocol. Cisco 1000 Series Integrated Services Router Software Configuration Guide 142 Control Router Access with Passwords and Privi...

**#3** [isr1100-sw-config.pdf - 1000 Series ISR looks for this file before finding the standard files-router-confg or the ciscortr.cfg.] (score 0.489)

> in the bootflash. If the file is not found in the bootflash, the router then looks for the standard files-router-confg and ciscortr.cfg. If none of the files are found, the router then checks for any inserted USB that may have stored these files in the same particular order. If there is a configurat...
