# ① 最速：OBSのネイティブHotkey運用（テンキーだけ）
- OBSで各テロップ／画像（ソース）に「表示の切り替え」ホットキーを割り当てる  
  例）  
  - Num1：マリカ図 ON/OFF  
  - Num2：「来ました来ました！」テロップ ON/OFF  
  - Num3：進捗バー ON/OFF  
- 外付けテンキーを挿す → そのキーに対応付けるだけ。  
- 長所：**設定2分・超安定・追加ソフト不要**  
- 短所：キーが増えると管理がやや煩雑（→②で解消）

# ② お手軽拡張：AutoHotkey（Windows）でレイヤ化
「テンキー → OBSの特定ソースをトグル」を**人間が覚えやすい配列**に再マップ。

**例：AutoHotkeyスクリプト（抜粋）**
```ahk
#SingleInstance Force
; テンキー → OBSホットキー送出（例：Ctrl+Alt+数字をOBS側に設定済み）
Numpad1:: Send, ^!1   ; 図ON/OFF
Numpad2:: Send, ^!2   ; テロップON/OFF
Numpad3:: Send, ^!3   ; 進捗バーON/OFF
NumpadAdd:: Send, ^!0 ; 全OFF（パニックボタン）
return
```
- OBS側では各ソースに「Ctrl+Alt+1」などを割当て。  
- 長所：**安定・安価・学習不要**  
- 代替（Mac）：Karabiner-Elementsで同等設定。

# ③ 本命：OBS WebSocket 連携（テンキー/音声どちらも）
OBSの**ソース表示**を**名前で直接ON/OFF**。配信プロファイルが変わっても壊れにくい。

## セットアップ
1) OBS → 「obs-websocket」有効化（OBS 28+は標準搭載）  
2) 設定→WebSocketサーバON、パスワード設定（例：`secret123`）  
3) Python 3 を用意し `pip install obs-websocket-py`  
4) 下の `config.json` と `controller.py` を同じフォルダに保存

**config.json（ソース名と動作のマッピング）**
```json
{
  "obs": {"host": "localhost", "port": 4455, "password": "secret123", "scene": "配信_本番"},
  "bindings": {
    "NUM1": {"source": "MK_コース図", "action": "toggle"},
    "NUM2": {"source": "TEL_来ました来ました", "action": "toggle"},
    "NUM3": {"source": "BAR_進捗", "action": "toggle"},
    "NUM0": {"action": "all_off"}
  },
  "voice": {
    "enabled": false,
    "wake": "OK アシスタント",
    "commands": {
      "コース出して": {"source": "MK_コース図", "action": "show"},
      "コース消して": {"source": "MK_コース図", "action": "hide"},
      "テロップきた": {"source": "TEL_来ました来ました", "action": "show"},
      "全部消して": {"action": "all_off"}
    }
  }
}
```

**controller.py（テンキー＋オプションで音声）**
```python
import json, sys, threading
from obswebsocket import obsws, requests as req

# --- 入力デバイス（テンキー）: pynput でグローバルフック ---
from pynput import keyboard

# 音声は任意（オフなら無視）
VOICE_ENABLED = False
try:
    import speech_recognition as sr
except:
    sr = None

with open("config.json", "r", encoding="utf-8") as f:
    CFG = json.load(f)

HOST = CFG["obs"]["host"]; PORT = CFG["obs"]["port"]; PWD = CFG["obs"]["password"]
SCENE = CFG["obs"]["scene"]
BIND = CFG["bindings"]
VOICE = CFG.get("voice", {})

def with_ws(fn):
    def wrap(*a, **k):
        ws = obsws(HOST, PORT, PWD)
        ws.connect()
        try:
            return fn(ws, *a, **k)
        finally:
            ws.disconnect()
    return wrap

@with_ws
def set_visible(ws, source, visible: bool):
    ws.call(req.SetSceneItemEnabled(sceneName=SCENE, sceneItemId=_get_item_id(ws, source), sceneItemEnabled=visible))

def _get_item_id(ws, name):
    items = ws.call(req.GetSceneItemList(sceneName=SCENE)).getSceneItems()
    for it in items:
        if it['sourceName'] == name:
            return it['sceneItemId']
    raise ValueError(f"source not found: {name}")

@with_ws
def all_off(ws):
    items = ws.call(req.GetSceneItemList(sceneName=SCENE)).getSceneItems()
    for it in items:
        if not it['isGroup']:
            ws.call(req.SetSceneItemEnabled(sceneName=SCENE, sceneItemId=it['sceneItemId'], sceneItemEnabled=False))

def toggle(source):
    # 現在状態取得→反転
    ws = obsws(HOST, PORT, PWD); ws.connect()
    try:
        items = ws.call(req.GetSceneItemList(sceneName=SCENE)).getSceneItems()
        for it in items:
            if it['sourceName'] == source:
                cur = it['sceneItemEnabled']
                ws.call(req.SetSceneItemEnabled(sceneName=SCENE, sceneItemId=it['sceneItemId'], sceneItemEnabled=not cur))
                return
        raise ValueError(f"source not found: {source}")
    finally:
        ws.disconnect()

def handle_action(act):
    if "action" in act and act["action"] == "all_off":
        all_off(); return
    source = act["source"]; action = act.get("action", "toggle")
    if action == "toggle": toggle(source)
    elif action == "show": set_visible(source, True)
    elif action == "hide": set_visible(source, False)

KEYMAP = {
    keyboard.KeyCode.from_vk(97): "NUM1",  # Numpad1
    keyboard.KeyCode.from_vk(98): "NUM2",
    keyboard.KeyCode.from_vk(99): "NUM3",
    keyboard.KeyCode.from_vk(96): "NUM0"
}
# 上記VKはWindows例。Mac/Linuxは必要に応じ変更（print(key)で同定）

def on_press(key):
    keyname = KEYMAP.get(key)
    if keyname and keyname in BIND:
        handle_action(BIND[keyname])

def kb_loop():
    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()

def voice_loop():
    if not VOICE_ENABLED or not sr: return
    r = sr.Recognizer()
    mic = sr.Microphone()
    wake = VOICE.get("wake", "")
    cmds = VOICE.get("commands", {})
    with mic as source:
        r.adjust_for_ambient_noise(source, duration=0.5)
    while True:
        with mic as source:
            audio = r.listen(source, phrase_time_limit=4)
        try:
            text = r.recognize_google(audio, language="ja-JP")
        except Exception:
            continue
        if wake and not text.startswith(wake):  # 起動語ありの場合
            continue
        if wake:
            text = text.replace(wake, "", 1).strip()
        for phrase, act in cmds.items():
            if phrase in text:
                handle_action(act); break

if __name__ == "__main__":
    VOICE_ENABLED = VOICE.get("enabled", False) and (sr is not None)
    threading.Thread(target=kb_loop, daemon=True).start()
    if VOICE_ENABLED:
        threading.Thread(target=voice_loop, daemon=True).start()
    print("Controller running. Press Ctrl+C to exit.")
    try:
        while True: pass
    except KeyboardInterrupt:
        sys.exit(0)
```

- **使い方**  
  - OBSでシーン名・ソース名を`config.json`と合わせる  
  - `python controller.py` を起動  
  - テンキー or 音声コマンドで表示切替  
- **声の例（起動語ON時）**  
  - 「OK アシスタント、コース出して」→ 図表示  
  - 「OK アシスタント、全部消して」→ 全クリア

> 音声の精度を上げたい場合はオフライン認識（Vosk など）に差し替え可能。  
> まずはテンキー主体で運用し、後から音声をONにするのが現実的です。

# ④（任意）字幕ワンタッチ
- OBS の「テキスト(GDI+)」ソースにプリセット文言（例：「来ました来ました！」）を複数用意  
- テンキーで個別トグル／`all_off`で一括OFF  
- 長文字幕は**ホットキーでシーン切替**（演出と同期しやすい）

---

## まとめ（選び方）
- **最速で今日回す** → ①（OBSホットキーのみ）  
- **キー配列を綺麗にしたい** → ②（AutoHotkey併用）  
- **将来拡張・声操作・安定同期** → ③（OBS WebSocket連携）
