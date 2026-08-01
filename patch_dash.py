import os

path = 'dashboard/src/components/TradesTable.tsx'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix desktop table
old_desktop = """                  ordered.map((t, i) => (
                    <tr key={i} className="hover:bg-white/5">
                      <td className="px-4 py-3 text-slate-400 font-mono text-xs">
                        {formatTime(t.exit_time)}
                      </td>
                      <td className="px-4 py-3 font-bold">{t.trade_type}</td>
                      <td className="px-4 py-3">{t.strike}</td>
                      <td className="px-4 py-3 font-mono text-xs">
                        ₹{t.entry_price.toFixed(2)} ➔ ₹{t.exit_price.toFixed(2)}
                      </td>"""

new_desktop = """                  ordered.map((t, i) => (
                    <tr key={i} className="hover:bg-white/5">
                      <td className="px-4 py-3 text-slate-400 font-mono text-xs">
                        {formatTime(t.exit_time)}
                      </td>
                      <td className="px-4 py-3 font-bold">{t.trade_type}</td>
                      <td className="px-4 py-3">{t.trade_type === "IRON_CONDOR" ? "Spread" : t.strike}</td>
                      <td className="px-4 py-3 font-mono text-xs">
                        {t.trade_type === "IRON_CONDOR" ? `Cr: ₹${t.entry_price.toFixed(1)} ➔ ₹${t.exit_price.toFixed(1)}` : `₹${t.entry_price.toFixed(2)} ➔ ₹${t.exit_price.toFixed(2)}`}
                      </td>"""

content = content.replace(old_desktop, new_desktop)

# Fix mobile card
old_mobile = """        <div className="flex items-center justify-between text-[11px] text-slate-400">
          <span>Strike {t.strike}</span>
          <span className="font-mono">
            ₹{t.entry_price} ➔ ₹{t.exit_price}
          </span>
        </div>"""

new_mobile = """        <div className="flex items-center justify-between text-[11px] text-slate-400">
          <span>{t.trade_type === "IRON_CONDOR" ? "Credit Spread" : `Strike ${t.strike}`}</span>
          <span className="font-mono">
            {t.trade_type === "IRON_CONDOR" ? `Cr: ₹${t.entry_price.toFixed(1)} ➔ ₹${t.exit_price.toFixed(1)}` : `₹${t.entry_price} ➔ ₹${t.exit_price}`}
          </span>
        </div>"""

content = content.replace(old_mobile, new_mobile)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("TradesTable.tsx patched successfully.")
