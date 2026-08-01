import re

log_path = 'logs/sniper_bot_SENSEX_2026-06-19.log'

with open(log_path, 'r') as f:
    lines = f.readlines()

in_3pm_zone = False
for line in lines:
    if '13:30:' in line:
        in_3pm_zone = True
    if '14:00:' in line:
        break
    
    if in_3pm_zone:
        if 'Strike' in line and ('CE:' in line or 'PE:' in line):
            print(line.strip())

