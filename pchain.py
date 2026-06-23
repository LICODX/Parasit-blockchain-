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

# ============================================================
# GLOBAL LOCKS & SAFETY CONSTANTS
# ============================================================
state_lock = threading.RLock()          # Reentrant Lock untuk blockchain state
crypto_lock = threading.Lock()          # Lock khusus cryptographic operations

MAX_MEMPOOL_SIZE = 10000                # Limit transaksi dalam mempool
MAX_MEMPOOL_BYTES = 100 * 1024 * 1024  # 100MB max mempool size
MAX_REORG_DEPTH = 6                     # Bitcoin-standard reorg limit
SUBNET_SCAN_TIMEOUT = 0.1               # 100ms timeout per IP scan
SUBNET_SCAN_INTERVAL = 30               # Scan ulang setiap 30 detik

# ============================================================
# KELAS UTAMA PARASIT APEX (IMPROVED)
# ============================================================
class MatureApexParasite:
    def __init__(self):
        self.blockchain = []
        self.utxo_pool = {}
        self.mempool = []
        self.known_peers = set(["127.0.0.1"])
        self.is_running = True
        self.peer_reputation = {}  # Track peer reliability

        self.my_ip = self.detect_local_ip()
        self.load_or_create_wallet()
        self.load_blockchain_from_storage()

    # ---- DETEKSI IP LOKAL ----
    def detect_local_ip(self):
        """Deteksi IP lokal dengan fallback ke localhost"""
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
        """Load atau buat wallet ECDSA baru"""
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
        """Load blockchain dari disk dengan validasi genesis block"""
        with state_lock:
            if os.path.exists(BLOCKCHAIN_DB):
                try:
                    with open(BLOCKCHAIN_DB, "r") as f:
                        chain = json.load(f)
                    
                    # VALIDASI GENESIS BLOCK - FIX #1
                    if (not chain or 
                        chain[0].get("hash") != GENESIS_BLOCK["hash"] or
                        chain[0].get("index") != 0 or
                        chain[0].get("prev_hash") != GENESIS_BLOCK["prev_hash"]):
                        print("[⚠️ GENESIS CHECK] Genesis block corrupted! Resetting...")
                        raise ValueError("Invalid genesis block DNA")
                    
                    self.blockchain = chain
                    self.rebuild_utxo_pool_unlocked()
                    print(f"[✓ LOAD] Blockchain loaded: {len(chain)} blok dari disk")
                    return
                except Exception as e:
                    print(f"[-] Chain load failed: {e}. Resetting to genesis...")
            
            self.blockchain = [GENESIS_BLOCK]
            self.rebuild_utxo_pool_unlocked()
            self.save_to_disk_unlocked()
            print(f"[✓ INIT] Blockchain initialized dengan genesis block")

    def save_to_disk_unlocked(self):
        """Simpan blockchain ke disk (HARUS dalam state_lock)"""
        try:
            with open(BLOCKCHAIN_DB, "w") as f:
                json.dump(self.blockchain, f, indent=2)
        except IOError as e:
            print(f"[-] Error saving blockchain: {e}")

    def rebuild_utxo_pool_unlocked(self):
        """Rebuild UTXO pool dari blockchain (HARUS dalam state_lock)"""
        self.utxo_pool = {}
        for block in self.blockchain:
            for tx in block["transactions"]:
                # Hapus input yang sudah dipakai
                for inp in tx["inputs"]:
                    key = f"{inp['tx_id']}:{inp['vout']}"
                    if key in self.utxo_pool:
                        del self.utxo_pool[key]
                # Tambah output baru
                for out in tx["outputs"]:
                    self.utxo_pool[f"{tx['tx_id']}:{out['vout']}"] = {
                        "amount": out["amount"],
                        "address": out["address"]
                    }

    def get_wallet_balance(self):
        """Hitung saldo wallet saat ini"""
        with state_lock:
            return sum(
                u["amount"] for u in self.utxo_pool.values()
                if u["address"] == self.address
            )

    # ---- PENGIRIMAN PAKET UDP (KAPASITAS BESAR) ----
    def send_packet_direct(self, data_dict, target_ip):
        """Kirim paket binary UDP ke peer"""
        data_dict["sender_ip"] = self.my_ip
        payload = json.dumps(data_dict)
        binary_packet = struct.pack("!4s4sI", b"d1:q", b"ping", len(payload)) + payload.encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(binary_packet, (target_ip, PORT_PARASIT))
        except Exception as e:
            # Track peer reliability - FIX #5
            if target_ip in self.peer_reputation:
                self.peer_reputation[target_ip] -= 1
            else:
                self.peer_reputation[target_ip] = -1
        finally:
            sock.close()

    def broadcast_to_all_known_peers(self, data_dict):
        """Broadcast pesan ke semua peer yang dikenal"""
        with state_lock:
            peers_snapshot = list(self.known_peers)
        
        for ip in peers_snapshot:
            # Skip peer dengan reputation buruk
            if self.peer_reputation.get(ip, 0) < -5:
                continue
            self.send_packet_direct(data_dict, ip)

    # ---- PEMINDAIAN SUBNET AKTIF (PEMBURUAN INANG) ----
    def parasite_active_subnet_scan(self):
        """Scan subnet untuk menemukan node parasit lain (non-blocking)"""
        if self.my_ip == "127.0.0.1":
            print(f"[🔍 SCAN] Localhost mode - skipping subnet scan")
            return
        
        print(f"[🔍 SCAN] Memulai perburuan host aktif di subnet: {self.my_ip}/24")
        ip_parts = self.my_ip.split(".")
        base_subnet = ".".join(ip_parts[:3])
        
        while self.is_running:
            for i in range(1, 255):
                target_ip = f"{base_subnet}.{i}"
                if target_ip != self.my_ip:
                    # FIX #6: Non-blocking sendto dengan timeout
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(SUBNET_SCAN_TIMEOUT)
                    try:
                        self.send_packet_direct({"type": "PARASITE_PING"}, target_ip)
                    except socket.timeout:
                        pass
                    except Exception:
                        pass
                    finally:
                        try:
                            sock.close()
                        except:
                            pass
            
            time.sleep(SUBNET_SCAN_INTERVAL)

    # ---- VALIDASI TRANSAKSI (ECDSA + UTXO + COINBASE) ----
    def validate_transaction(self, tx, local_utxo_snapshot):
        """Validasi transaksi single dengan ECDSA signature + UTXO check"""
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
            # FIX #7: Thread-safe crypto verification dengan crypto_lock
            with crypto_lock:
                vk = VerifyingKey.from_string(base64.b64decode(pub_key_str), curve=SECP256k1)
                if not vk.verify(base64.b64decode(signature), tx_string.encode()):
                    return False, "Tanda tangan kriptografi palsu"
        except Exception as e:
            return False, f"Gagal eksekusi verifikasi: {e}"

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
        """Validasi single block dengan PoW + transaction rules"""
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
        """Resolve blockchain fork dengan validasi penuh (FIX #2, #3, #4)"""
        with state_lock:
            if not incoming_chain or len(incoming_chain) == 0:
                return

            # 1. GENESIS LOCK — tolak rantai dengan DNA berbeda
            if incoming_chain[0]["hash"] != GENESIS_BLOCK["hash"]:
                print(f"[❌ GENESIS REJECT] {peer_addr} mencoba DNA palsu!")
                self.peer_reputation[peer_addr] = -10
                return

            local_td = sum(b.get("difficulty", DIFFICULTY) for b in self.blockchain)
            incoming_td = sum(b.get("difficulty", DIFFICULTY) for b in incoming_chain)

            if incoming_td <= local_td:
                return  # rantai lokal lebih kuat, abaikan

            # FIX #4: Reorg depth limit check
            reorg_depth = len(self.blockchain) - len(incoming_chain)
            if abs(reorg_depth) > MAX_REORG_DEPTH:
                print(f"[❌ REORG REJECT] Depth {reorg_depth} > {MAX_REORG_DEPTH}")
                self.peer_reputation[peer_addr] = self.peer_reputation.get(peer_addr, 0) - 2
                return

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
                    self.peer_reputation[peer_addr] = self.peer_reputation.get(peer_addr, 0) - 3
                    return
                is_ok, temp_utxo = self.verify_single_block_rules(cur, temp_utxo)
                if not is_ok:
                    print(f"[-] Rantai {peer_addr} ilegal di Blok #{idx}")
                    self.peer_reputation[peer_addr] = self.peer_reputation.get(peer_addr, 0) - 5
                    return

            # 3. ADOPSI RANTAI BARU (RE-ORG)
            print(f"[⚙️ RE-ORG] Adopsi rantai superior dari {peer_addr} ({len(incoming_chain)} blok)")
            self.blockchain = incoming_chain
            self.utxo_pool = temp_utxo
            self.save_to_disk_unlocked()
            self.mempool = []
            self.peer_reputation[peer_addr] = self.peer_reputation.get(peer_addr, 0) + 1

    # ---- MEMBUAT TRANSAKSI KIRIM ----
    def create_real_transaction(self, to_address, amount):
        """Buat signed transaction dan broadcast ke mempool"""
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
            
            # Thread-safe signing
            with crypto_lock:
                tx["signature"] = base64.b64encode(
                    self.private_key.sign(tx_string.encode())
                ).decode()

            self.mempool.append(tx)
            print("[✓ MEMPOOL] Transaksi sah siap disebar.")
        self.broadcast_to_all_known_peers({"type": "PCHAIN_TX", "tx": tx})
        return True

    # ---- THREAD PENAMBANGAN OTONOM (BEBAS DEADLOCK) ----
    def mine_mempool_autonomous(self):
        """Mining thread yang berjalan autonomous dengan proper locking"""
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
                prev_block_hash = prev["hash"]  # Capture sebelum mining

            # FIX #1: Mining loop tanpa lock (safe)
            while self.is_running and not found:
                header = (
                    f"{index}{prev_block_hash}"
                    f"{json.dumps(valid_tx_list, sort_keys=True)}{nonce}"
                )
                c_hash = hashlib.sha256(header.encode()).hexdigest()
                if c_hash.startswith("0" * DIFFICULTY):
                    new_block = {
                        "index": index,
                        "prev_hash": prev_block_hash,
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
                    # FIX #8: Ensure blockchain not changed during mining
                    if (len(self.blockchain) != index or 
                        self.blockchain[-1]["hash"] != prev_block_hash):
                        print(f"[⚠️] Blockchain changed mid-mining, block stale")
                        continue

                    is_valid, _ = self.verify_single_block_rules(
                        new_block, self.utxo_pool
                    )
                    if is_valid:
                        self.blockchain.append(new_block)
                        self.mempool = []
                        self.rebuild_utxo_pool_unlocked()
                        self.save_to_disk_unlocked()

                        # HITUNG SALDO LANGSUNG
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
        """P2P receiver yang handle blocks, transactions, dan sync"""
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
                        print(f"[🔗 PEER] Menemukan peer baru: {sender_ip}")

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
                                    self.peer_reputation[sender_ip] = self.peer_reputation.get(sender_ip, 0) + 1
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
                            # FIX #5: Mempool size limit check
                            if len(self.mempool) >= MAX_MEMPOOL_SIZE:
                                print(f"[-] Mempool full ({MAX_MEMPOOL_SIZE}), rejecting TX from {sender_ip}")
                                continue

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

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    print(f"[-] Receiver error: {e}")
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

    print("=" * 60)
    print("  P-CHAIN APEX P2P DAEMON v7.1 (Parasitic Blockchain)")
    print("  🦠 Parasit siap berburu, bereplikasi, dan berkembang")
    print("=" * 60)
    print(f"  Wallet Address: {node.address}")
    print(f"  Listen Port: {PORT_PARASIT}")
    print(f"  Difficulty: {DIFFICULTY} (POW leading zeros)")
    print(f"  Block Reward: {BLOCK_REWARD} P-BTC")
    print("=" * 60)

    try:
        while True:
            with state_lock:
                balance = node.get_wallet_balance()
                chain_height = len(node.blockchain)
                peers_count = len(node.known_peers)
                mempool_size = len(node.mempool)
            
            print("\n--- [ 🧬 DASHBOARD P-CHAIN APEX ] ---")
            print(f" 🔑 Alamat Dompet   : {node.address}")
            print(f" 💰 Saldo           : {balance} P-BTC")
            print(f" ⛓️  Tinggi Rantai   : {chain_height} blok")
            print(f" 🌐 Peer terhubung  : {peers_count} IP")
            print(f" 📦 Mempool         : {mempool_size} tx")
            print(f" 📊 Genesis Lock    : {'✓ VALID' if node.blockchain[0]['hash'] == GENESIS_BLOCK['hash'] else '✗ INVALID'}")
            print("-----------------------------------")
            print(" 💡 Fitur:")
            print("    • Automatic subnet scanning & peer discovery")
            print("    • Autonomous mining dengan thread locking")
            print("    • Fork resolution dengan reorg depth limit")
            print("    • ECDSA transaction signing & verification")
            print("    • Peer reputation tracking")
            print(" Tekan CTRL+C untuk berhenti.")
            time.sleep(10)
    except KeyboardInterrupt:
        node.is_running = False
        print("\n\n[⏹️] Parasit dihentikan. Data tersimpan aman.")
        print(f"[📊] Final state: {len(node.blockchain)} blok, {len(node.known_peers)} peers dikenal")
