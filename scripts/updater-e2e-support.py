"""Operações Windows do ensaio; somente instalação e processos sob o sandbox TEMP."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import sqlite3
import hashlib
from contextlib import closing
import threading
import time

import psutil


def sandbox(value):
    root=Path(value).resolve()
    assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert root.name.startswith('horus-updater-e2e-')
    return root


def processes(root):
    found=[]
    for p in psutil.process_iter(['pid','name','exe','cmdline']):
        try:
            exe=Path(p.info['exe'] or '').resolve()
            profile=next((a.split('=',1)[1].strip('"') for a in p.info['cmdline'] or [] if a.startswith('--user-data-dir=')),None)
            own_webview=p.info['name'].lower()=='msedgewebview2.exe' and profile and Path(profile).resolve().is_relative_to(root)
            if exe.is_relative_to(root/'app') or own_webview:
                found.append({'pid':p.pid,'name':p.info['name'],'exe':str(exe)})
        except (OSError,psutil.Error): pass
    return found


def close(pid):
    user=ctypes.WinDLL('user32',use_last_error=True)
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    user.EnumWindows.argtypes=[callback,wintypes.LPARAM]
    @callback
    def visit(hwnd,_):
        owner=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value==pid and user.IsWindowVisible(hwnd):user.PostMessageW(hwnd,0x0010,0,0)
        return True
    user.EnumWindows(visit,0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['init','close','stop','watch','backups','verify','fingerprint','writer','restore'])
    parser.add_argument('root')
    args=parser.parse_args();root=sandbox(args.root)
    if args.command=='init':
        folder=root/'factory/scripts';(folder/'fabrica').mkdir(parents=True,exist_ok=True)
        (folder/'fabrica/__init__.py').write_text('')
        (folder/'fabrica/cli.py').write_text('import time\ntime.sleep(90)\nprint("{}")\n')
        (folder/'pyproject.toml').write_text('[project]\nname="updater-fixture"\nversion="0.0.0"\n')
        (folder/'.venv/Scripts').mkdir(parents=True,exist_ok=True)
        shutil.copyfile(sys.executable,folder/'.venv/Scripts/python.exe')
        shutil.copyfile(Path(sys.executable).parents[1]/'pyvenv.cfg',folder/'.venv/pyvenv.cfg')
    elif args.command in {'close','stop'}:
        owned=processes(root)
        for p in owned:
            if p['name']=='horus-desktop.exe':close(p['pid'])
        until=time.monotonic()+45
        while processes(root) and time.monotonic()<until:time.sleep(.1)
        remaining=processes(root)
        if args.command=='stop':
            for p in remaining:
                try:psutil.Process(p['pid']).kill()
                except psutil.Error:pass
            until=time.monotonic()+10
            while processes(root) and time.monotonic()<until:time.sleep(.1)
        assert not processes(root),json.dumps({'orphan_processes':processes(root)})
    elif args.command=='watch':
        entries=[];watched=set();threads=[]
        parent=psutil.Process(os.getppid())
        def wait(p,scenario):
            try:code=p.wait(timeout=300)
            except psutil.Error:code=None
            entries.append({'kind':'backend_exit','pid':p.pid,'code':code,'scenario':scenario})
        while not (root/'stop-watch').exists() and parent.is_running():
            for p in psutil.process_iter(['pid','name','cmdline','exe']):
                try:
                    if p.pid in watched:continue
                    command=p.info['cmdline'] or []
                    if p.info['name'].lower().startswith('horus-') and '-installer' in p.info['name'].lower() and '/UPDATE' in command:
                        # Não registra tokens/env; somente processos NSIS de atualização.
                        if any('/P'==s for s in command):
                            entries.append({'kind':'nsis_update','pid':p.pid,'command':command});watched.add(p.pid)
                    if p.info['name']=='horus-backend.exe' and Path(p.info['exe']).resolve().is_relative_to(root/'app'):
                        scenario=(root/'watch-scenario.txt').read_text() if (root/'watch-scenario.txt').exists() else 'normal'
                        watched.add(p.pid);thread=threading.Thread(target=wait,args=(p,scenario),daemon=True);thread.start();threads.append(thread)
                except (psutil.Error,OSError,TypeError):pass
            time.sleep(.05)
        for thread in threads:thread.join(timeout=3)
        (root/'process-evidence.json').write_text(json.dumps(entries,indent=2))
    elif args.command=='backups':
        from horus_backend.local_data_export import verify_export
        packages=list((root/'update-backups').glob('*.zip'))
        assert packages,'Backup não criado'
        for package in packages:assert verify_export(package)['ok']
        print(json.dumps({'backups':len(packages),'verified':True}))
    elif args.command=='fingerprint':
        with closing(sqlite3.connect(root/'data/horus.sqlite3')) as db:
            result={}
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
                quoted='"'+name.replace('"','""')+'"'
                rows=sorted(json.dumps(list(row),ensure_ascii=False,default=str) for row in db.execute(f'SELECT * FROM {quoted}'))
                result[name]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
        print(json.dumps(result))
    elif args.command=='writer':
        writes=0;largest=0
        parent=psutil.Process(os.getppid())
        with closing(sqlite3.connect(root/'data/horus.sqlite3',timeout=10)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            while not (root/'stop-writer').exists() and parent.is_running():
                writes+=1
                db.execute('INSERT INTO media VALUES(?,?,?,?,?,?)',(f'wal-writer-{writes}',str(root/f'data/writer-{writes}.txt'),'writer-fixture',41,1,'2026-11-16'))
                db.commit()
                wal=root/'data/horus.sqlite3-wal'
                if wal.exists():largest=max(largest,wal.stat().st_size)
                if writes==1:(root/'writer-ready').write_text('ready')
                time.sleep(.01)
        (root/'writer-result.json').write_text(json.dumps({'writes':writes,'wal_max_bytes':largest}))
    elif args.command=='restore':
        from horus_backend.local_data_export import restore_local_data
        packages=sorted((root/'update-backups').glob('*.zip'),key=lambda p:p.stat().st_mtime)
        restored=root/'restored'
        result=restore_local_data(packages[-1],restored)
        with closing(sqlite3.connect(restored/'horus.sqlite3')) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            copied=db.execute("SELECT COUNT(*) FROM media WHERE id LIKE 'wal-writer-%'").fetchone()[0]
        assert copied>0,'WAL do escritor não entrou no backup'
        assert not (restored/'exports/example.mp4').exists(),'Mídia grande não deve entrar no ZIP'
        (root/'restore-result.json').write_text(json.dumps({**result,'writer_rows':copied,'integrity':'ok'}))
        print(json.dumps({'ok':True,'writer_rows':copied,'integrity':'ok'}))
    elif args.command=='verify':
        result=subprocess.run([sys.executable,str(Path(__file__).parent/'check-installer-data.py'),str(root/'data'),'verify'],check=True,capture_output=True,text=True)
        print(result.stdout.strip())


if __name__=='__main__':main()
