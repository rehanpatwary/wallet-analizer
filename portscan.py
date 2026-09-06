#!/usr/bin/env python3
"""Full port scan on suspected Proxmox nodes."""
import socket, concurrent.futures

def scan_port(ip, port, timeout=2):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return ip, port, True
    except:
        return ip, port, False

TARGETS = ["10.10.20.7", "10.10.20.10", "10.10.20.11"]
PORTS = list(range(1, 1025)) + [3000, 3006, 8080, 8443, 9000, 9090, 9130, 9139, 19130, 19139, 19001, 19011, 19021, 19030, 19031, 19032, 19033]

print("Scanning for blockbook / blockchain nodes on unusual ports...")
found = []
for ip in TARGETS:
    print(f"\nScanning {ip}...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=200) as executor:
        futures = {executor.submit(scan_port, ip, port): port for port in PORTS}
        for future in concurrent.futures.as_completed(futures):
            ip_res, port_res, open_res = future.result()
            if open_res:
                print(f"  OPEN: {ip_res}:{port_res}")
                found.append((ip_res, port_res))

print(f"\nTotal open ports found: {len(found)}")
for ip, port in found:
    print(f"  {ip}:{port}")
