# Engine check on a device, without the UI: start the manager, print what it sees, send a
# zero-hop advert and a Public message. Run with: mpremote connect PORT resume run tools/engine_probe.py
import gc
import sys
import time

APP_DIR = "/apps/eu.axistem.micropymesh"
ROUNDS = 9           # report rounds
INTERVAL_S = 10
SEND = True          # zero-hop advert + one Public message after the first round

if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)
import meshcore_crypto  # noqa: E402
from meshcore_manager import MeshCoreManager  # noqa: E402

print("NATIVE", meshcore_crypto.NATIVE)
m = MeshCoreManager.get_instance()
m.start()
t0 = time.ticks_ms()
while m.is_keygen_running() or not m._radio_ready:
    if time.ticks_diff(time.ticks_ms(), t0) > 30000:
        print("radio not ready after 30 s", m.radio_status())
        break
    time.sleep_ms(200)
print("identity", m.get_identity()[0].hex() if m.has_identity() else None, "name", m.nickname())
print("preset", m.radio_preset())

for rnd in range(ROUNDS):
    time.sleep(INTERVAL_S)
    gc.collect()
    st = m.radio_stats()
    rs = m.radio_status()
    print("--- round %d  free=%d  mode=%s rx=%d tx=%d reinits=%d" % (
        rnd, gc.mem_free(), rs["mode"], rs["rx_count"], rs["tx_count"], rs["reinits"]))
    print("stats rx_on=%s noise=%s peak=%s pkt/h=%s tx_air=%s last_rx_s=%s" % (
        st["rx_on"], st["noise_dbm"], st["peak_rssi_30m"], st["packets_per_h"],
        st["tx_air_pct"], st["last_rx_s"]))
    for n in m.get_learned_companions()[:8]:
        print("node %s %-8s %-16s verified=%s snr=%s hops=%s" % (
            n.get("id"), n.get("type_name"), n.get("name"), n.get("verified"),
            n.get("snr"), n.get("hops")))
    for msg in m.get_messages("Public")[-3:]:
        print("Public %s: %s  (in=%s snr=%s hops=%s tx=%s heard=%s unheard=%s)" % (
            msg.get("sender"), msg.get("text"), msg.get("incoming"), msg.get("snr"),
            msg.get("hops"), msg.get("tx"), msg.get("heard"), msg.get("unheard")))
    if SEND and rnd == 0:
        print("advert", m.advertise(flood=False))
        print("public", m.send_group_text("Public", "probe %d" % time.time()))

m.stop()
print("stopped")
