import os

path = 'dashboard/src/app/page.tsx'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old_val = 'value={stats?.open_position ? `${stats.open_position.strike}` : "—"}'
new_val = 'value={stats?.open_position ? (stats.open_position.trade_type === "IRON_CONDOR" ? "Spread" : `${stats.open_position.strike}`) : "—"}'

content = content.replace(old_val, new_val)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("page.tsx patched successfully.")
