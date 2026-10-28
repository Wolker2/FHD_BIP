#!/usr/bin/env python3
"""Package the original BIP sources without a bundled Python installation.

Optionally include a Windows EXE produced by the provided GitHub build workflow.
The standalone Python application sources are not changed by this packager.
"""
from pathlib import Path
import argparse,hashlib,json,zipfile
ROOT=Path(__file__).resolve().parent;PREFIX='Mods/FHD_MODS/FHD_BIP/'
def load(path):return json.loads(Path(path).read_text('utf-8'))
def hashes(files):return {name:hashlib.sha256(data).hexdigest()for name,data in sorted(files.items())}
def build(root=ROOT):
    root=Path(root);files={}
    for name in load(root/'original-file-sha256.json'):
        if name=='ModuleInfo.txt':data=(root/'SOURCE/ModuleInfo.txt').read_bytes().decode('utf-8').encode('cp1251')
        else:data=(root/name).read_bytes()
        files[name]=data
    return files
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--verify-original',action='store_true');p.add_argument('--windows-exe',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    files=build()
    if a.verify_original and hashes(files)!=load(ROOT/'original-file-sha256.json'):raise SystemExit('Original source file hashes differ')
    if a.windows_exe:
        data=a.windows_exe.read_bytes()
        if data[:2]!=b'MZ':raise SystemExit('Expected a Windows PE executable')
        files['COMPANION/FHD_BIP_v0.8.0.exe']=data
    output=a.output or ROOT/'dist'/('FHD_BIP-v0.8.0-Windows-install.zip'if a.windows_exe else'FHD_BIP-v0.8.0-source-install.zip')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(PREFIX+name,(2026,1,1,0,0,0));info.external_attr=0o644<<16
            z.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    print(json.dumps({'files':len(files),'original_sources_verified':a.verify_original,'windows_exe_included':bool(a.windows_exe),'output':str(output)},indent=2))
if __name__=='__main__':main()
