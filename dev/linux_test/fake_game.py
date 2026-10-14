import socket, time
time.sleep(1)
s = socket.create_connection(('127.0.0.1', 13328))
end = time.time() + 30
while time.time() < end and s.recv(65536): pass
