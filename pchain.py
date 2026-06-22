import os
import sys
import socket
import hashlib
import time
import base64
import json
import struct
import threading
from ecdsa import SigningKey, SECP256k1, VerifyingKey, BadSignatureError

# ============================================================
# DNA MUTLAK GLOBAL (GENESIS BLOCK HARDCODED)
# ============================================================
GENESIS_BLOCK = {
    "index": 0,
    "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "transactions": [{
        "tx_id": "genesis_parasite_v7_apex",
        "inputs": [],
        "outputs": [{"vout": 0, "amount": 1000000, "address": "1PGenesisMasterWallet"}]
    }],
    "nonce": 92143,
    "hash": "0000f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0",
    "timestamp": 1719000000,
    "difficulty": 4
}

PORT_PARASIT = 6881
DIFFICULTY = 4          # awalan hash '0000'
BLOCK_REWARD = 50
BLOCKCHAIN_DB = "pchain_apex_ledger.json"
WALLET_FILE = "pchain_apex_wallet.json"

# Reentrant Lock (RLock) — mencegah deadlock antar fungsi yang saling memanggil
state_lock = threading.RLock()

# ============================================================
# KELAS UTAMA PARASIT APEX
# ============================================================
class MatureApexParasite:
    def __init__(self):
        self.blockchain = []
        self.utxo_pool = {}
        self.mempool = []
        self.known_peers = set(["127.0.0.1"])
        self.is_running = True

        self.my_ip = self.detect_local_ip()
        self.load_or_create_wallet()
        self.load_blockchain_from_storage()

    # ---- DETEKSI IP LOKAL ----
    def detect_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    # ---- MANAJEMEN DOMPET ----
    def load_or_create_wallet(self):
        if os.path.exists(WALLET_FILE):
            with open(WALLET_FILE, "r") as f:
                data = json.load(f)
                self.private_key = SigningKey.from_string(
                    bytes.fromhex(data["private_key_hex"]), curve=SECP256k1
                )
        else:
            self.private_key = SigningKey.generate(curve=SECP256k1)
            with open(WALLET_FILE, "w") as f:
                json.dump({"private_key_hex": self.private_key.to_string().hex()}, f)

        self.public_key = self.private_key.get_verifying_key()
        self.address = "1P" + hashlib.sha256(self.public_key.to_string()).hexdigest()[:12]

    # ---- PERSISTENSI BLOCKCHAIN ----
    def load_blockchain_from_storage(self):
        with state_lock:
            if os.path.exists(BLOCKCHAIN_DB):
                try:
                    with open(BLOCKCHAIN_DB, "r") as f:
                        self.blockchain = json.load(f)
                    self.rebuild_utxo_pool_unlocked()
                    return
                except Exception:
                    pass
            self.blockchain = [GENESIS_BLOCK]
            self.rebuild_utxo_pool_unlocked()
            self.save_to_disk_unlocked()

    def save_to_disk_unlocked(self):
        with open(BLOCKCHAIN_DB, "w") as f:
            json.dump(self.blockchain, f, indent=2)

    def rebuild_utxo_pool_unlocked(self):
        self.utxo_pool = {}
        for block in self.blockchain:
            for tx in block["transactions"]:
                for inp in tx["inputs"]:
                    key = f"{inp['tx_id']}:{inp['vout']}"
                    if key in self.utxo_pool:
                        del self.utxo_pool[key]
                for out in tx["outputs"]:
                    self.utxo_pool[f"{tx['tx_id']}:{out['vout']}"] = {
                        "amount": out["amount"],
                        "address": out["address"]
                    }

    def get_wallet_balance(self):
        with state_lock:
            return sum(
                u["amount"] for u in self.utxo_pool.values()
                if u["address"] == self.address
            )

    # ---- PENGIRIMAN PAKET UDP (KAPASITAS BESAR) ----
    def send_packet_direct(self, data_dict, target_ip):
        data_dict["sender_ip"] = self.my_ip
        payload = json.dumps(data_dict)
        # !4s4sI : 4 byte magic, 4 byte command, 4 byte unsigned int panjang
        binary_packet = struct.pack("!4s4sI", b"d1:q", b"ping", len(payload)) + payload.encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(binary_packet, (target_ip, PORT_PARASIT))
        except Exception:
            pass
        finally:
            sock.close()

    def broadcast_to_all_known_peers(self, data_dict):
        with state_lock:
            peers_snapshot = list(self.known_peers)
        for ip in peers_snapshot:
            self.send_packet_direct(data_dict, ip)

    # ---- PEMINDAIAN SUBNET AKTIF (PEMBURUAN INANG) ----
    def parasite_active_subnet_scan(self):
        print(f"[🔍 SCAN] Memulai perburuan host aktif di subnet: {self.my_ip}/24")
        while self.is_running:
            if self.my_ip == "127.0.0.1":
                time.sleep(10)
                continue
            ip_parts = self.my_ip.split(".")
            base_subnet = ".".join(ip_parts[:3])
            for i in range(1, 255):
                target_ip = f"{base_subnet}.{i}"
                if target_ip != self.my_ip:
                    self.send_packet_direct({"type": "PARASITE_PING"}, target_ip)
            time.sleep(30)  # siklus perburuan setiap 30 detik

    # ---- VALIDASI TRANSAKSI (ECDSA + UTXO + COINBASE) ----
    def validate_transaction(self, tx, local_utxo_snapshot):
        # Jika tidak ada input -> transaksi coinbase (reward penambang)
        if not tx["inputs"]:
            if len(tx["outputs"]) != 1 or tx["outputs"][0]["amount"] != BLOCK_REWARD:
                return False, "Korupsi nilai Coinbase Reward"
            return True, local_utxo_snapshot

        tx_copy = tx.copy()
        signature = tx_copy.pop("signature", None)
        pub_key_str = tx_copy.pop("pub_key", None)
        if not signature or not pub_key_str:
            return False, "Data tanda tangan tidak lengkap"

        tx_string = json.dumps(tx_copy, sort_keys=True)
        try:
            vk = VerifyingKey.from_string(base64.b64decode(pub_key_str), curve=SECP256k1)
            if not vk.verify(base64.b64decode(signature), tx_string.encode()):
                return False, "Tanda tangan kriptografi palsu"
        except Exception:
            return False, "Gagal eksekusi verifikasi"

        total_in = 0
        for inp in tx["inputs"]:
            key = f"{inp['tx_id']}:{inp['vout']}"
            if key not in local_utxo_snapshot:
                return False, "UTXO tidak tersedia (Double Spending)"
            total_in += local_utxo_snapshot[key]["amount"]
            del local_utxo_snapshot[key]

        total_out = sum(o["amount"] for o in tx["outputs"])
        if total_in < total_out or total_out <= 0:
            return False, "Nilai nominal tidak seimbang"

        for out in tx["outputs"]:
            local_utxo_snapshot[f"{tx['tx_id']}:{out['vout']}"] = {
                "amount": out["amount"],
                "address": out["address"]
            }

        return True, local_utxo_snapshot

    # ---- VALIDASI ATURAN SATU BLOK (PoW + TRANSAKSI) ----
    def verify_single_block_rules(self, block, current_utxo_snapshot):
        header = (
            f"{block['index']}{block['prev_hash']}"
            f"{json.dumps(block['transactions'], sort_keys=True)}{block['nonce']}"
        )
        calc_hash = hashlib.sha256(header.encode()).hexdigest()
        if not calc_hash.startswith("0" * DIFFICULTY) or calc_hash != block["hash"]:
            return False, "PoW atau Hash salah"

        snapshot = current_utxo_snapshot.copy()
        for i, tx in enumerate(block["transactions"]):
            if i == 0 and tx["inputs"]:
                return False, "Transaksi pertama wajib Coinbase"
            is_ok, snapshot = self.validate_transaction(tx, snapshot)
            if not is_ok:
                return False, f"Transaksi ke-{i} tidak valid"
        return True, snapshot

    # ---- RESOLUSI FORK (GENESIS LOCK + TOTAL DIFFICULTY + PREV_HASH) ----
    def resolve_full_chain_conflict(self, incoming_chain, peer_addr):
        with state_lock:
            if not incoming_chain or len(incoming_chain) == 0:
                return

            # 1. GENESIS LOCK — tolak rantai dengan DNA berbeda
            if incoming_chain[0]["hash"] != GENESIS_BLOCK["hash"]:
                print(f"[❌ GENESIS REJECT] {peer_addr} mencoba DNA palsu!")
                return

            local_td = sum(b.get("difficulty", DIFFICULTY) for b in self.blockchain)
            incoming_td = sum(b.get("difficulty", DIFFICULTY) for b in incoming_chain)

            if incoming_td <= local_td:
                return  # rantai lokal lebih kuat, abaikan

            # 2. VALIDASI STRUKTUR PENUH: prev_hash berantai & semua blok
            temp_utxo = {
                f"{GENESIS_BLOCK['transactions'][0]['tx_id']}:0": {
                    "amount": 1000000,
                    "address": "1PGenesisMasterWallet"
                }
            }
            for idx in range(1, len(incoming_chain)):
                cur = incoming_chain[idx]
                prev = incoming_chain[idx - 1]
                if cur["prev_hash"] != prev["hash"]:
                    print(f"[-] Rantai {peer_addr} putus prev_hash di Blok #{idx}")
                    return
                is_ok, temp_utxo = self.verify_single_block_rules(cur, temp_utxo)
                if not is_ok:
                    print(f"[-] Rantai {peer_addr} ilegal di Blok #{idx}")
                    return

            # 3. ADOPSI RANTAI BARU (RE-ORG)
            print(f"[⚙️ RE-ORG] Adopsi rantai superior dari {peer_addr} ({len(incoming_chain)} blok)")
            self.blockchain = incoming_chain
            self.utxo_pool = temp_utxo
            self.save_to_disk_unlocked()
            self.mempool = []

    # ---- MEMBUAT TRANSAKSI KIRIM ----
    def create_real_transaction(self, to_address, amount):
        with state_lock:
            inputs = []
            total_input = 0
            for key, utxo in self.utxo_pool.items():
                if utxo["address"] == self.address:
                    tx_id, vout = key.split(":")
                    inputs.append({"tx_id": tx_id, "vout": int(vout)})
                    total_input += utxo["amount"]
                    if total_input >= amount:
                        break
            if total_input < amount:
                print("[-] Saldo tidak mencukupi.")
                return False

            tx_id = hashlib.sha256(
                f"{self.address}{to_address}{amount}{time.time()}".encode()
            ).hexdigest()[:16]

            outputs = [{"vout": 0, "amount": amount, "address": to_address}]
            change = total_input - amount
            if change > 0:
                outputs.append({"vout": 1, "amount": change, "address": self.address})

            tx = {
                "tx_id": tx_id,
                "inputs": inputs,
                "outputs": outputs,
                "pub_key": base64.b64encode(self.public_key.to_string()).decode()
            }
            tx_string = json.dumps(tx, sort_keys=True)
            tx["signature"] = base64.b64encode(
                self.private_key.sign(tx_string.encode())
            ).decode()

            self.mempool.append(tx)
            print("[✓ MEMPOOL] Transaksi sah siap disebar.")
        self.broadcast_to_all_known_peers({"type": "PCHAIN_TX", "tx": tx})
        return True

    # ---- THREAD PENAMBANGAN OTONOM (BEBAS DEADLOCK) ----
    def mine_mempool_autonomous(self):
        while self.is_running:
            time.sleep(0.5)
            with state_lock:
                if not self.mempool:
                    continue

                prev = self.blockchain[-1]
                index = prev["index"] + 1

                # Coinbase (hadiah penambang)
                cb_id = hashlib.sha256(
                    f"coinbase_{index}_{time.time()}".encode()
                ).hexdigest()[:16]
                coinbase_tx = {
                    "tx_id": cb_id,
                    "inputs": [],
                    "outputs": [{"vout": 0, "amount": BLOCK_REWARD, "address": self.address}]
                }

                valid_tx_list = [coinbase_tx]
                snapshot = self.utxo_pool.copy()
                for tx in self.mempool:
                    ok, snap_up = self.validate_transaction(tx, snapshot)
                    if ok:
                        valid_tx_list.append(tx)
                        snapshot = snap_up

                if len(valid_tx_list) == 1:
                    self.mempool = []
                    continue

                nonce = 0
                found = False
                new_block = None

                while self.is_running:
                    with state_lock:
                        if len(self.blockchain) >= index:
                            break
                        header = (
                            f"{index}{prev['hash']}"
                            f"{json.dumps(valid_tx_list, sort_keys=True)}{nonce}"
                        )
                        c_hash = hashlib.sha256(header.encode()).hexdigest()
                        if c_hash.startswith("0" * DIFFICULTY):
                            new_block = {
                                "index": index,
                                "prev_hash": prev["hash"],
                                "transactions": valid_tx_list,
                                "nonce": nonce,
                                "hash": c_hash,
                                "timestamp": int(time.time()),
                                "difficulty": DIFFICULTY
                            }
                            found = True
                            break
                        nonce += 1

                if found and new_block:
                    with state_lock:
                        is_valid, _ = self.verify_single_block_rules(
                            new_block, self.utxo_pool
                        )
                        if is_valid:
                            self.blockchain.append(new_block)
                            self.mempool = []
                            self.rebuild_utxo_pool_unlocked()
                            self.save_to_disk_unlocked()

                            # HITUNG SALDO LANGSUNG (tanpa panggil get_wallet_balance agar aman)
                            balance = sum(
                                u["amount"] for u in self.utxo_pool.values()
                                if u["address"] == self.address
                            )
                            print(f"\n[⛏️ BLOCK FOUND] Blok #{index} lahir! Saldo: {balance} P-BTC")
                            self.broadcast_to_all_known_peers(
                                {"type": "PCHAIN_BLOCK", "block": new_block}
                            )

    # ---- THREAD PENERIMA JARINGAN (RELAY + FORK HANDLING) ----
    def internal_p2p_receiver(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", PORT_PARASIT))
            while self.is_running:
                try:
                    data, addr = sock.recvfrom(65535)
                    msg = json.loads(data[10:].decode())
                    sender_ip = addr[0]

                    if sender_ip not in self.known_peers:
                        with state_lock:
                            self.known_peers.add(sender_ip)

                    if msg["type"] == "PARASITE_PING":
                        self.send_packet_direct({"type": "PARASITE_PONG"}, sender_ip)

                    elif msg["type"] == "PCHAIN_BLOCK":
                        incoming_b = msg["block"]
                        with state_lock:
                            local_height = len(self.blockchain)

                            # Kasus A: blok berikutnya secara linier
                            if incoming_b["index"] == local_height:
                                is_ok, _ = self.verify_single_block_rules(
                                    incoming_b, self.utxo_pool
                                )
                                if is_ok and incoming_b["prev_hash"] == self.blockchain[-1]["hash"]:
                                    self.blockchain.append(incoming_b)
                                    self.rebuild_utxo_pool_unlocked()
                                    self.save_to_disk_unlocked()
                                    print(f"[📡 RELAY] Blok #{incoming_b['index']} dari {sender_ip} diadopsi")
                                    # Tularkan ke peer lain
                                    self.broadcast_to_all_known_peers(msg)

                            # Kasus B: rantai lebih tinggi — minta sinkronisasi penuh
                            elif incoming_b["index"] > local_height:
                                print(f"[📡 FORK] {sender_ip} memiliki rantai lebih tinggi. Minta sync...")
                                self.send_packet_direct(
                                    {"type": "REQUEST_CHAIN_SYNC"}, sender_ip
                                )

                    elif msg["type"] == "PCHAIN_TX":
                        with state_lock:
                            ok, _ = self.validate_transaction(
                                msg["tx"], self.utxo_pool.copy()
                            )
                            if ok and msg["tx"] not in self.mempool:
                                self.mempool.append(msg["tx"])
                                print(f"[📡 TX VECTOR] TX dari {sender_ip} masuk mempool")
                                # Relay transaksi ke seluruh koloni
                                self.broadcast_to_all_known_peers(msg)

                    elif msg["type"] == "REQUEST_CHAIN_SYNC":
                        with state_lock:
                            self.send_packet_direct(
                                {"type": "RESPONSE_CHAIN_SYNC", "chain": self.blockchain},
                                sender_ip
                            )

                    elif msg["type"] == "RESPONSE_CHAIN_SYNC":
                        self.resolve_full_chain_conflict(msg["chain"], sender_ip)

                except Exception:
                    continue
        finally:
            sock.close()


# ============================================================
# EKSEKUSI UTAMA (DAEMON DENGAN 3 THREAD)
# ============================================================
if __name__ == "__main__":
    node = MatureApexParasite()

    t_recv = threading.Thread(target=node.internal_p2p_receiver, daemon=True)
    t_mine = threading.Thread(target=node.mine_mempool_autonomous, daemon=True)
    t_scan = threading.Thread(target=node.parasite_active_subnet_scan, daemon=True)

    t_recv.start()
    t_mine.start()
    t_scan.start()

    print("================================================")
    print("  P-CHAIN APEX P2P DAEMON v7.0 (Apex Predator)")
    print("  Parasit siap berburu dan bereplikasi.")
    print("================================================")

    try:
        while True:
            print("\n--- [ DASHBOARD ] ---")
            print(f" Alamat Dompet   : {node.address}")
            print(f" Saldo           : {node.get_wallet_balance()} P-BTC")
            print(f" Tinggi Rantai   : {len(node.blockchain)} blok")
            print(f" Peer terhubung  : {len(node.known_peers)} IP")
            print(f" Mempool         : {len(node.mempool)} tx")
            print("----------------------")
            print(" Ketik 'transfer' di jendela lain (belum diimplementasikan di sini)")
            print(" Tekan CTRL+C untuk berhenti.")
            time.sleep(10)
    except KeyboardInterrupt:
        node.is_running = False
        print("\n[-] Parasit dihentikan. Data tersimpan aman.")
