#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DMK Ritim Testi (Tek Dosya Saha Ölçüm Aracı)
- Kelime: "vatan"
- Rollover (Tuş Örtüşmesi + Enter Örtüşmesi): FIFO kuyruk ve gecikmeli teslim ile düzeltilmiş.
- Skorlama: Dwell (Basılı Kalma) ve Flight (Uçuş) süreleri ayrı eşiklerle değerlendirilir.
"""

import sys
import time
import json
import statistics
from collections import deque, defaultdict

try:
    from pynput import keyboard
except ImportError:
    print("[!] 'pynput' kütüphanesi bulunamadı. Kurmak için: pip install pynput")
    sys.exit(1)

TARGET_WORD = "wsxedc"
ENROLL_COUNT = 5
GENUINE_TRIALS = 10
IMPOSTOR_TRIALS = 10

MAD_NORMAL_FACTOR = 1.4826
MIN_DWELL_MAD_FLOOR = 0.008   # 8 ms alt sınır
MIN_FLIGHT_MAD_FLOOR = 0.012  # 12 ms alt sınır

# Ayrı Dwell ve Flight Eşikleri
DWELL_AVG_THRESHOLD = 2.5
DWELL_MAX_THRESHOLD = 4.5
FLIGHT_AVG_THRESHOLD = 2.2
FLIGHT_MAX_THRESHOLD = 4.5


def calculate_scaled_mad(values, floor):
    med = statistics.median(values)
    raw_mad = statistics.median([abs(x - med) for x in values])
    scaled_mad = max(raw_mad * MAD_NORMAL_FACTOR, floor)
    return round(med, 6), round(scaled_mad, 6)


def build_template(enroll_runs, word=TARGET_WORD):
    template = {"dwell": [], "flight": []}
    for i in range(len(word)):
        med, smad = calculate_scaled_mad([r["dwells"][i] for r in enroll_runs], MIN_DWELL_MAD_FLOOR)
        template["dwell"].append({"med": med, "smad": smad})
    for i in range(len(word) - 1):
        med, smad = calculate_scaled_mad([r["flights"][i] for r in enroll_runs], MIN_FLIGHT_MAD_FLOOR)
        template["flight"].append({"med": med, "smad": smad})
    return template


def evaluate_run(template, run,
                 d_avg_th=DWELL_AVG_THRESHOLD, d_max_th=DWELL_MAX_THRESHOLD,
                 f_avg_th=FLIGHT_AVG_THRESHOLD, f_max_th=FLIGHT_MAX_THRESHOLD):
    d_scores = [
        abs(run["dwells"][i] - template["dwell"][i]["med"]) / template["dwell"][i]["smad"]
        for i in range(len(run["dwells"]))
    ]
    f_scores = [
        abs(run["flights"][i] - template["flight"][i]["med"]) / template["flight"][i]["smad"]
        for i in range(len(run["flights"]))
    ]
    all_scores = d_scores + f_scores

    d_avg, d_max = sum(d_scores) / len(d_scores), max(d_scores)
    f_avg, f_max = sum(f_scores) / len(f_scores), max(f_scores)
    c_avg, c_max = sum(all_scores) / len(all_scores), max(all_scores)

    dwell_ok = (d_avg < d_avg_th) and (d_max < d_max_th)
    flight_ok = (f_avg < f_avg_th) and (f_max < f_max_th)
    accepted_split = dwell_ok and flight_ok
    accepted_legacy = (c_avg < 2.5) and (c_max < 4.0)

    return {
        "accepted": accepted_split,
        "accepted_legacy": accepted_legacy,
        "dwell_ok": dwell_ok,
        "flight_ok": flight_ok,
        "dwell_avg": round(d_avg, 3),
        "dwell_max": round(d_max, 3),
        "flight_avg": round(f_avg, 3),
        "flight_max": round(f_max, 3),
        "combined_avg": round(c_avg, 3),
        "combined_max": round(c_max, 3),
    }


class RolloverSafeCollector:
    """
    FIFO tabanlı fiziksel tuş takibi:
    1. Aynı tuş basılı tutulurken işletim sisteminin ürettiği otomatik tekrarları (auto-repeat) yoksayar.
    2. Hızlı yazımda önceki tuş bırakılmadan sonraki tuşa basılmasını (N-key rollover / negatif flight) korur.
    3. Son harf ('n') henüz bırakılmadan Enter'a basılırsa, son harf bırakıldığı an kaydı tamamlar.
    """
    def __init__(self, target_word=TARGET_WORD):
        self.target_word = target_word
        self.events = []
        self.fifo_queues = defaultdict(deque)
        self.held_keys = set()
        self.dirty_run = False
        self.pending_enter = False
        self.completed_runs = []
        self.target_count = 0
        self.stage_label = ""

    def reset_current(self):
        self.events.clear()
        self.fifo_queues.clear()
        self.held_keys.clear()
        self.dirty_run = False
        self.pending_enter = False

    @staticmethod
    def _extract_key_id(key, ch):
        vk = getattr(key, "vk", None)
        return f"vk_{vk}" if vk is not None else f"ch_{ch.lower()}"

    def record_press(self, ch, ts, key_id):
        if key_id in self.held_keys:
            return  # OS auto-repeat filtresi
        self.held_keys.add(key_id)
        idx = len(self.events)
        self.events.append({
            "char": ch,
            "key_id": key_id,
            "t_press": ts,
            "t_release": None
        })
        self.fifo_queues[key_id].append(idx)

    def record_release(self, ts, key_id):
        self.held_keys.discard(key_id)
        if not self.fifo_queues[key_id]:
            return
        idx = self.fifo_queues[key_id].popleft()
        self.events[idx]["t_release"] = ts

    def _all_released(self):
        return bool(self.events) and all(e["t_release"] is not None for e in self.events)

    def _try_finalize_run(self):
        ordered = sorted(self.events, key=lambda x: x["t_press"])
        typed_word = "".join(e["char"] for e in ordered)

        if (self.dirty_run or
            typed_word != self.target_word or
            len(ordered) != len(self.target_word) or
            any(e["t_release"] is None for e in ordered)):
            print(f"  [!] Geçersiz yazım ('{typed_word}'). Tekrar '{self.target_word}' yazıp ENTER'a basın.")
            self.reset_current()
            return True

        dwells = [round(e["t_release"] - e["t_press"], 6) for e in ordered]
        flights = [
            round(ordered[i + 1]["t_press"] - ordered[i]["t_release"], 6)
            for i in range(len(ordered) - 1)
        ]

        self.completed_runs.append({"dwells": dwells, "flights": flights})
        cur = len(self.completed_runs)
        print(f"  [+] {self.stage_label}: {cur}/{self.target_count} kaydedildi "
              f"(Dwell ort: {sum(dwells)/len(dwells)*1000:.1f} ms | "
              f"Flight ort: {sum(flights)/len(flights)*1000:.1f} ms)")
        self.reset_current()

        return cur < self.target_count

    def on_press(self, key):
        ts = time.perf_counter()
        if key == keyboard.Key.backspace:
            self.dirty_run = True
            return
        if key == keyboard.Key.enter:
            self.pending_enter = True
            return
        ch = getattr(key, "char", None)
        if ch is None:
            return
        key_id = self._extract_key_id(key, ch)
        self.record_press(ch, ts, key_id)

    def on_release(self, key):
        ts = time.perf_counter()
        if key == keyboard.Key.enter:
            self.pending_enter = True
            if not self.events or self._all_released():
                return self._try_finalize_run()
            return True

        ch = getattr(key, "char", None)
        if ch is not None:
            key_id = self._extract_key_id(key, ch)
            self.record_release(ts, key_id)

        if self.pending_enter and self._all_released():
            return self._try_finalize_run()
        return True

    def collect(self, count, label):
        self.completed_runs = []
        self.target_count = count
        self.stage_label = label
        self.reset_current()
        print(f"\n>>> [{label}] Lütfen '{self.target_word}' kelimesini {count} kez yazın (Her yazım sonrası ENTER):")
        with keyboard.Listener(on_press=self.on_press, on_release=self.on_release) as listener:
            listener.join()
        return list(self.completed_runs)


def main():
    print("=================================================================")
    print(" DMK RİTİM TESTİ — ROLLOVER DÜZELTİLMİŞ & DWELL/FLIGHT AYRI EŞİKLİ")
    print("=================================================================")

    try:
        impostor_count_str = input("Kaç yabancı kişi (saldırgan/impostor) test edilecek? [Varsayılan: 2]: ").strip()
        impostor_count = int(impostor_count_str) if impostor_count_str else 2
    except ValueError:
        impostor_count = 2

    collector = RolloverSafeCollector(TARGET_WORD)

    # 1. Kayıt (Enrollment)
    enroll_runs = collector.collect(ENROLL_COUNT, "AŞAMA 1 - ŞABLON KAYDI (ASIL KULLANICI)")
    template = build_template(enroll_runs)

    # 2. FRR Testi (Asıl Kullanıcı Doğrulama)
    genuine_runs = collector.collect(GENUINE_TRIALS, "AŞAMA 2 - FRR TESTİ (ASIL KULLANICI)")
    genuine_evals = [evaluate_run(template, r) for r in genuine_runs]
    for i, ev in enumerate(genuine_evals, 1):
        durum = "KABUL" if ev["accepted"] else "RED"
        print(f"  Asıl #{i:02d}: {durum} | Dwell(ort={ev['dwell_avg']:.2f}, max={ev['dwell_max']:.2f}) | "
              f"Flight(ort={ev['flight_avg']:.2f}, max={ev['flight_max']:.2f})")

    # 3. FAR Testi (Yabancı Kullanıcılar)
    impostor_evals = []
    impostor_raw = {}
    for idx in range(1, impostor_count + 1):
        input(f"\n[!] Klavyeyi YABANCI {idx}. KİŞİYE devredin ve başlamak için ENTER'a basın...")
        imp_runs = collector.collect(IMPOSTOR_TRIALS, f"AŞAMA 3.{idx} - FAR TESTİ (YABANCI {idx})")
        impostor_raw[f"impostor_{idx}"] = imp_runs
        for j, r in enumerate(imp_runs, 1):
            ev = evaluate_run(template, r)
            impostor_evals.append(ev)
            durum = "YANLIŞ KABUL (FAR!)" if ev["accepted"] else "DOĞRU RED"
            print(f"  Yabancı {idx} #{j:02d}: {durum} | Dwell(ort={ev['dwell_avg']:.2f}, max={ev['dwell_max']:.2f}) | "
                  f"Flight(ort={ev['flight_avg']:.2f}, max={ev['flight_max']:.2f})")

    # 4. Sonuç Matrisi
    total_g = len(genuine_evals)
    total_i = len(impostor_evals)

    fr_split = sum(1 for e in genuine_evals if not e["accepted"])
    fa_split = sum(1 for e in impostor_evals if e["accepted"])

    fr_legacy = sum(1 for e in genuine_evals if not e["accepted_legacy"])
    fa_legacy = sum(1 for e in impostor_evals if e["accepted_legacy"])

    print("\n=================================================================")
    print("                 GERÇEK SAHA FAR / FRR SONUÇ TABLOSU             ")
    print("=================================================================")
    print(f"1) AYRIK EŞİK (Dwell<{DWELL_AVG_THRESHOLD}/{DWELL_MAX_THRESHOLD}, Flight<{FLIGHT_AVG_THRESHOLD}/{FLIGHT_MAX_THRESHOLD}):")
    print(f"   - FRR (Asıl Kullanıcıyı Yanlış Red): %{(fr_split/total_g)*100:.1f} ({fr_split}/{total_g})")
    if total_i > 0:
        print(f"   - FAR (Yabancıyı Yanlış Kabul)     : %{(fa_split/total_i)*100:.1f} ({fa_split}/{total_i})")

    print(f"\n2) ESKİ BİRLEŞİK EŞİK (V7 Ort<2.5, Max<4.0):")
    print(f"   - FRR (Asıl Kullanıcıyı Yanlış Red): %{(fr_legacy/total_g)*100:.1f} ({fr_legacy}/{total_g})")
    if total_i > 0:
        print(f"   - FAR (Yabancıyı Yanlış Kabul)     : %{(fa_legacy/total_i)*100:.1f} ({fa_legacy}/{total_i})")

    output_data = {
        "template": template,
        "summary": {
            "split_threshold": {
                "frr_percent": round((fr_split / total_g) * 100, 2),
                "far_percent": round((fa_split / total_i) * 100, 2) if total_i else None,
            },
            "legacy_threshold": {
                "frr_percent": round((fr_legacy / total_g) * 100, 2),
                "far_percent": round((fa_legacy / total_i) * 100, 2) if total_i else None,
            }
        },
        "genuine_evals": genuine_evals,
        "impostor_evals": impostor_evals,
        "raw_enroll": enroll_runs,
        "raw_genuine": genuine_runs,
        "raw_impostors": impostor_raw,
    }

    with open("dmk_saha_sonuclari.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    print("\n[+] Tüm ham veriler ve skorlar 'dmk_saha_sonuclari.json' dosyasına kaydedildi.")


if __name__ == "__main__":
    main()