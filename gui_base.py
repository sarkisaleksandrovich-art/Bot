"""Tkinter training tab for shared base navigation.

Storage: data/base/nav.json contains base_to_locations={"png": ...}, exactly
seven locations [{"slot": 1, "name": ..., "png": ..., "floors":
[{"name": ..., "png": ...}]}], and enter_dungeon={"png": ...}. PNG files
are in data/base/. JSON is written atomically with a temporary file and replace.
This module only teaches regions; it never farms or performs automatic clicks.
"""
from __future__ import annotations
import json, os, time, tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any
try:
    import cv2
except ImportError:
    cv2 = None
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None
try:
    from bot.config import load_config
    from bot.adb_controller import ADBController
    from bot.logging_setup import setup_logging
    from bot.screenshot import ScreenshotProvider
except Exception:
    load_config = ADBController = setup_logging = ScreenshotProvider = None

BASE_DIR = Path("data/base")
NAV_PATH = BASE_DIR / "nav.json"

def _empty_nav():
    return {"base_to_locations": {"png": ""}, "locations": [{"slot": i, "name": "", "png": "", "floors": []} for i in range(1, 8)], "enter_dungeon": {"png": ""}}

def _load_nav():
    data = _empty_nav()
    try:
        with NAV_PATH.open(encoding="utf-8") as f: loaded = json.load(f)
        if isinstance(loaded, dict): data.update({k: v for k, v in loaded.items() if k in data})
    except (OSError, json.JSONDecodeError): pass
    old = {int(x.get("slot")): x for x in data.get("locations", []) if isinstance(x, dict) and str(x.get("slot", "")).isdigit()}
    data["locations"] = [old.get(i, _empty_nav()["locations"][i-1]) for i in range(1, 8)]
    for x in data["locations"]: x.setdefault("floors", [])
    return data

def _save_nav(data):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = NAV_PATH.with_name(f".{NAV_PATH.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        tmp.replace(NAV_PATH)
    finally:
        if tmp.exists(): tmp.unlink()

def _provider():
    if ScreenshotProvider is None or load_config is None: raise RuntimeError("ScreenshotProvider unavailable")
    config = load_config("config.yaml")
    logger = setup_logging(config) if setup_logging else None
    adb = ADBController(config, logger); adb.connect()
    return ScreenshotProvider(adb, config, logger)

def capture_region(parent, prompt) -> tuple[Any, tuple[int, int, int, int]] | None:
    """Return (crop_bgr, bbox), selected with the mouse, or None on cancel."""
    if cv2 is None or Image is None:
        messagebox.showerror("Ошибка", "Нужны OpenCV и Pillow.", parent=parent); return None
    try: frame = _provider().capture()
    except Exception as exc:
        messagebox.showerror("Скриншот", str(exc), parent=parent); return None
    if frame is None or not getattr(frame, "size", 0): return None
    h, w = frame.shape[:2]; scale = min(1.0, 900 / w, 650 / h)
    display = cv2.resize(frame, (int(w*scale), int(h*scale))) if scale != 1 else frame
    image = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(display, cv2.COLOR_BGR2RGB)))
    win = tk.Toplevel(parent); win.title(str(prompt)); win.transient(parent)
    canvas = tk.Canvas(win, width=display.shape[1], height=display.shape[0], cursor="crosshair"); canvas.pack(padx=8, pady=8)
    canvas.create_image(0, 0, anchor="nw", image=image); state = {"start": None, "rect": None, "result": None}
    def down(e):
        state["start"] = (max(0, min(e.x, display.shape[1])), max(0, min(e.y, display.shape[0])))
        if state["rect"]: canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(*state["start"], *state["start"], outline="red", width=2)
    def move(e):
        if state["start"] and state["rect"]: canvas.coords(state["rect"], *state["start"], e.x, e.y)
    def save():
        if not state["start"] or not state["rect"]: return
        x1,y1,x2,y2 = canvas.coords(state["rect"]); left,right = sorted((max(0,int(x1/scale)),min(w,int(x2/scale)))); top,bottom = sorted((max(0,int(y1/scale)),min(h,int(y2/scale))))
        if right > left and bottom > top: state["result"] = (frame[top:bottom,left:right].copy(), (left,top,right,bottom)); win.destroy()
    canvas.bind("<ButtonPress-1>", down); canvas.bind("<B1-Motion>", move)
    buttons = ttk.Frame(win); buttons.pack(fill="x", padx=8, pady=(0,8)); ttk.Button(buttons,text="Сохранить",command=save).pack(side="left"); ttk.Button(buttons,text="Отмена",command=win.destroy).pack(side="right")
    win.grab_set(); win.wait_window(); return state["result"]

def _write_png(crop, prefix):
    BASE_DIR.mkdir(parents=True, exist_ok=True); path = BASE_DIR / f"{prefix}_{time.time_ns()}.png"
    if cv2 is None or not cv2.imwrite(str(path), crop): raise RuntimeError(f"PNG write failed: {path}")
    return str(path)

class NavigationTab(ttk.Frame):
    """Training tab with four base lessons, seven locations and nested floors."""
    def __init__(self, parent, app):
        super().__init__(parent, padding=12); self.app=app; self.data=_load_nav()
        self.location_vars=[tk.StringVar(value=x.get("name", "")) for x in self.data["locations"]]; self.status_vars=[tk.StringVar(value="✓" if x.get("png") else "✗") for x in self.data["locations"]]; self.floor_location=tk.StringVar(value="1"); self._build()
    def _build(self):
        ttk.Label(self,text="Базовые знания",font=("TkDefaultFont",15,"bold")).pack(anchor="w"); ttk.Label(self,text="Выделяйте области мышью; кликов в эмуляторе не выполняется.",wraplength=900).pack(anchor="w",pady=(2,10))
        r=ttk.Frame(self); r.pack(fill="x"); ttk.Button(r,text="Назначить вход в локации",command=self._base_to_locations).pack(side="left"); self.base_status=ttk.Label(r,text="✓" if self.data["base_to_locations"].get("png") else "✗"); self.base_status.pack(side="left",padx=8)
        box=ttk.LabelFrame(self,text="7 локаций"); box.pack(fill="x",pady=8)
        for i in range(7):
            row=ttk.Frame(box); row.pack(fill="x",pady=2); ttk.Label(row,text=f"{i+1}.",width=3).pack(side="left"); ttk.Entry(row,textvariable=self.location_vars[i],width=24).pack(side="left"); ttk.Button(row,text="Привязать к скриншоту",command=lambda n=i:self._bind_location(n)).pack(side="left",padx=4); ttk.Label(row,textvariable=self.status_vars[i],width=2).pack(side="left")
        floors=ttk.LabelFrame(self,text="Этаж"); floors.pack(fill="x",pady=8); ttk.Label(floors,text="Локация:").pack(side="left"); combo=ttk.Combobox(floors,textvariable=self.floor_location,values=[str(i) for i in range(1,8)],state="readonly",width=5); combo.pack(side="left"); combo.bind("<<ComboboxSelected>>",lambda e:self._refresh_floors()); ttk.Button(floors,text="Добавить этаж",command=self._add_floor).pack(side="left",padx=4); self.floor_list=tk.Listbox(floors,height=5,width=55); self.floor_list.pack(side="left"); self._refresh_floors()
        r=ttk.Frame(self); r.pack(fill="x",pady=3); ttk.Button(r,text="Назначить кнопку В данж",command=self._enter_dungeon).pack(side="left"); ttk.Label(r,text="✓" if self.data["enter_dungeon"].get("png") else "✗").pack(side="left",padx=8); ttk.Button(self,text="Проверить цепочку",command=self._check_chain).pack(anchor="w",pady=8)
    def _capture_save(self,prompt,prefix):
        selected=capture_region(self,prompt)
        if selected is None:return None
        try:return _write_png(selected[0],prefix)
        except Exception as exc: messagebox.showerror("Сохранение",str(exc),parent=self); return None
    def _base_to_locations(self):
        png=self._capture_save("Выделите кнопку перехода к локациям","base_to_locations")
        if png:self.data["base_to_locations"]={"png":png}; _save_nav(self.data); self.base_status.config(text="✓")
    def _bind_location(self,i):
        png=self._capture_save(f"Локация {i+1}: выделите иконку",f"location_{i+1:02d}")
        if png:self.data["locations"][i].update(slot=i+1,name=self.location_vars[i].get().strip(),png=png); _save_nav(self.data); self.status_vars[i].set("✓")
    def _add_floor(self):
        i=int(self.floor_location.get())-1; name=simpledialog.askstring("Этаж","Название этажа:",parent=self)
        if not name:return
        png=self._capture_save(f"{name}: выделите область этажа",f"location_{i+1:02d}_floor")
        if png:self.data["locations"][i].setdefault("floors",[]).append({"name":name.strip(),"png":png}); _save_nav(self.data); self._refresh_floors()
    def _refresh_floors(self):
        if not hasattr(self,"floor_list"):return
        self.floor_list.delete(0,tk.END)
        for x in self.data["locations"][int(self.floor_location.get())-1].get("floors",[]):self.floor_list.insert(tk.END,f"{x.get('name','')}: {x.get('png','')}")
    def _enter_dungeon(self):
        png=self._capture_save("Выделите кнопку В данж","enter_dungeon")
        if png:self.data["enter_dungeon"]={"png":png}; _save_nav(self.data)
    def _check_chain(self):
        try:
            import nav; checker=getattr(nav,"check_chain",None)
            if callable(checker): messagebox.showinfo("Цепочка",str(checker()),parent=self)
            else: messagebox.showinfo("Цепочка","Функция проверки цепочки в nav.py отсутствует.",parent=self)
        except Exception as exc: messagebox.showerror("Цепочка",str(exc),parent=self)

__all__=["capture_region","NavigationTab"]
