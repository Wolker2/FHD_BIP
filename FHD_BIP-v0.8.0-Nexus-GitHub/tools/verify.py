#!/usr/bin/env python3
"""Verify preserved original BIP sources and run supplied offline/loopback tests."""
from pathlib import Path
import argparse,ast,importlib.metadata,json,os,platform,shutil,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from build import build,hashes,load
def verify():
    checks=[]
    def check(condition,label):
        assert condition,label;checks.append(label)
    original=load(ROOT/'original-file-sha256.json')
    with tempfile.TemporaryDirectory() as folder:
        clean=Path(folder)
        for name in original:
            source=ROOT/('SOURCE/ModuleInfo.txt'if name=='ModuleInfo.txt'else name)
            target=clean/('SOURCE/ModuleInfo.txt'if name=='ModuleInfo.txt'else name)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        shutil.copyfile(ROOT/'original-file-sha256.json',clean/'original-file-sha256.json')
        payload=build(clean)
    check(hashes(payload)==original,'All 54 retained original files match uploaded SHA-256 after isolated source-only packaging')
    check(len(payload)==54,'Exactly 54 original application/source/doc files retained')
    python_files=[p for p in (ROOT/'COMPANION').rglob('*.py')if '__pycache__'not in p.parts]
    for path in python_files:ast.parse(path.read_text('utf-8-sig'),filename=str(path))
    check(len(python_files)==39,'All 39 original Python files parse, including 37 application modules, launcher and tests')
    check(not any(name.lower().endswith(('.pyc','.exe','.dll','.pyd','.pkg','.msix','.scr','.dat'))for name in payload),'No environment/cache/installer/compiled bridge payload in source package')
    check(not any('/.venv/'in name or'/__pycache__/'in name for name in payload),'Virtual environment and bytecode caches excluded')
    check(payload['ModuleInfo.txt'].decode('cp1251')==(ROOT/'SOURCE/ModuleInfo.txt').read_bytes().decode('utf-8'),'CP1251 metadata preserves original bytes and line endings')
    check('ver.0.8.0 Alpha' in payload['ModuleInfo.txt'].decode('cp1251'),'Original version retained')
    for name in ['COMPANION/INSTALL_AND_RUN.bat','COMPANION/BUILD_EXE.bat','COMPANION/requirements-runtime.txt','COMPANION/requirements-build.txt','COMPANION/launcher.py']:
        check(name in payload,'Original launch/build input retained: '+name)
    env=os.environ.copy()
    # Avoid reading a user's real profile, credentials or data while verifying.
    env={k:v for k,v in env.items()if not k.startswith(('FHD_BIP_','FHD_AICOMPANION_'))}
    with tempfile.TemporaryDirectory() as profile:
        env['LOCALAPPDATA']=profile
        extra=[str(Path(p).resolve())for p in env.get('PYTHONPATH','').split(os.pathsep)if p]
        env['PYTHONPATH']=os.pathsep.join([str(ROOT/'COMPANION/src'),*extra]);env['PYTHONDONTWRITEBYTECODE']='1'
        command=[sys.executable,'-m','unittest','discover','-s',str(ROOT/'COMPANION/tests'),'-v']
        result=subprocess.run(command,env=env,capture_output=True,text=True)
    check(result.returncode==0,'Original unit/integration test suite passes')
    output=result.stdout+result.stderr
    check('Ran 20 tests'in output,'All 20 supplied tests executed')
    source_files=[p for p in ROOT.rglob('*')if p.is_file()and not any(x in ['.git','.venv','.build-venv','__pycache__','build','dist']for x in p.relative_to(ROOT).parts)]
    check(len(source_files)<=100,'Source package stays within 100-file limit')
    return {'status':'passed','checks_passed':len(checks),'checks':checks,'original_files_retained':len(payload),
        'original_python_files':len(python_files),'application_modules':37,'unittest_cases':20,'test_output':output,
        'clean_install_payload_sha256':hashes(payload),'application_code_changes':False,
        'external_provider_api_called':False,'network_test_scope':'One supplied integration test uses a loopback-only HTTP server; other provider tests use mocks',
        'windows_exe_built_here':False,'windows_game_gui_microphone_screen_or_credential_store_exercised':False,
        'missing_game_bridge':'No CFG/SCR/DLL or native bridge source in the supplied archive; not synthesized',
        'environment':{'python':platform.python_version(),'platform':sys.platform,'packages':{n:importlib.metadata.version(n)for n in ['websockets','Pillow','sounddevice']}},
        'limits':['Windows PyInstaller freezing is provided as an automated GitHub job and was not run on this Linux host.','Live provider compatibility, actual screen/audio capture, Credential Manager and game command execution are not established by these tests.']}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path);a=p.parse_args();report=verify()
    if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k]for k in ['status','checks_passed','original_files_retained','unittest_cases']},indent=2))
