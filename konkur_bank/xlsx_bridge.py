"""Convert specially structured PsychoPlan Excel workbooks to validated CSV-ZIP packages.
Read-only OOXML parser using stdlib: no Python packages required on Windows.
Rejects macros, formulas, external links, unsafe archives and malformed rows.
"""
import argparse
import csv
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

from .importer_validation import ALLOWED, ValidationError, validate

M='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
P='{http://schemas.openxmlformats.org/package/2006/relationships}'
MAX_XLSX=20*1024*1024
MAX_EXPANDED=50*1024*1024
MAX_ROWS=15010
MAX_COLS=80


def cell_number(ref):
    m=re.fullmatch(r'([A-Z]+)([0-9]+)',ref)
    if not m:raise ValidationError('Invalid cell reference: '+str(ref))
    col=0
    for ch in m.group(1):col=col*26+ord(ch)-64
    row=int(m.group(2))
    if row<1 or row>MAX_ROWS or col<1 or col>MAX_COLS:
        raise ValidationError('Cell out of range: '+str(ref))
    return row,col


def parse_workbook(source):
    path=Path(source)
    if not path.is_file() or path.stat().st_size>MAX_XLSX:
        raise ValidationError('Excel file missing or too large')
    with zipfile.ZipFile(path) as z:
        infos=z.infolist()
        if len(infos)>250:raise ValidationError('Too many XLSX entries')
        total=sum(i.file_size for i in infos)
        if total>MAX_EXPANDED:raise ValidationError('Expanded Excel too large')
        names=[x.filename for x in infos]
        if len(set(names))!=len(names):raise ValidationError('Duplicate Excel ZIP members')
        for n in names:
            p=PurePosixPath(n)
            if p.is_absolute() or '..' in p.parts or '\\' in n:raise ValidationError('Unsafe XLSX member')
            if ('vbaProject' in n or 'externalLinks/' in n or 'connections.xml' in n):
                raise ValidationError('Macros, external links and connections are not allowed')
        work=ET.fromstring(z.read('xl/workbook.xml'))
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets={r.get('Id'):r.get('Target') for r in rels.findall(P+'Relationship')}
        shared=[]
        if 'xl/sharedStrings.xml' in names:
            strings=ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in strings.findall(M+'si'):
                shared.append(''.join(t.text or '' for t in si.iter(M+'t')))
        result={}
        for sh in work.findall(M+'sheets/'+M+'sheet'):
            name=sh.get('name')
            relid=sh.get(R+'id')
            target=targets.get(relid,'')
            if not target:raise ValidationError('Excel worksheet target missing '+str(name))
            if target.startswith('/'):
                target=target.lstrip('/')
            elif not target.startswith('xl/'):
                target='xl/'+target
            target=target.replace('xl/./','xl/')
            if target not in names:
                raise ValidationError('Worksheet file missing '+name+' '+target)
            ws=ET.fromstring(z.read(target))
            sparse={}
            for c in ws.findall('.//'+M+'sheetData/'+M+'row/'+M+'c'):
                if c.find(M+'f') is not None:
                    raise ValidationError('Formula cells forbidden: '+str(name)+' '+str(c.get('r')))
                row,col=cell_number(c.get('r',''))
                ty=c.get('t','')
                if ty=='inlineStr':
                    value=''.join(t.text or '' for t in c.iter(M+'t'))
                else:
                    v=c.find(M+'v')
                    value=v.text or '' if v is not None else ''
                    if ty=='s':
                        index=int(value)
                        if index>=len(shared):raise ValidationError('Invalid shared string index')
                        value=shared[index]
                sparse.setdefault(row,{})[col]=value
            if not sparse:
                result[name]=[];continue
            ncols=max(max(a) for a in sparse.values())
            require_rows=max(sparse)
            rows=[]
            for r in range(1,require_rows+1):
                row=[sparse.get(r,{}).get(c,'') for c in range(1,ncols+1)]
                if any(str(v).strip() for v in row):rows.append(row)
            result[name]=rows
        return result


def convert_xlsx_bytes(path):
    sheets=parse_workbook(path)
    manifest_rows=sheets.get('manifest')
    if not manifest_rows or len(manifest_rows)<3:raise ValidationError('Missing manifest worksheet')
    if manifest_rows[0][0].strip().lower()!='key':raise ValidationError('Manifest first header must be key')
    manifest={}
    for row in manifest_rows[1:]:
        key=row[0].strip()
        val=row[1] if len(row)>1 else ''
        if not key:continue
        if key in manifest:raise ValidationError('Duplicate manifest key '+key)
        manifest[key]=val
    if 'expected_question_count' in manifest:
        manifest['expected_question_count']=int(manifest['expected_question_count'])
    if 'batch_version' in manifest:
        manifest['batch_version']=int(manifest['batch_version'])
    if 'may_publish' in manifest:
        if str(manifest['may_publish']).strip().lower() not in ('false','0'):
            raise ValidationError('Excel stage must explicitly forbid publishing')
        manifest['may_publish']=False
    typ=manifest.get('package_type')
    if typ not in ALLOWED:raise ValidationError('Unknown Excel package type')
    expected={n[:-4] for n in ALLOWED[typ]}|{'manifest'}
    if set(sheets)!=expected:
        raise ValidationError('Excel sheets must exactly match '+repr(sorted(expected)))
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        z.writestr('README_FA.txt','این ZIP از قالب اکسل سایکوپلن ساخته شده است. پیش‌نویس؛ اجازه انتشار ندارد.\n')
        for n in sorted(ALLOWED[typ]):
            grid=sheets[n[:-4]]
            if not grid:raise ValidationError('Missing header in Excel sheet '+n)
            header=[str(c).strip() for c in grid[0]]
            while header and not header[-1]:header.pop()
            if not header or any(not c for c in header) or len(header)!=len(set(header)):
                raise ValidationError('Invalid header in '+n)
            out=io.StringIO(newline='')
            writer=csv.writer(out)
            writer.writerow(header)
            for row in grid[1:]:
                if not any(str(c).strip() for c in row):continue
                if len(row)>len(header) and any(str(c).strip() for c in row[len(header):]):
                    raise ValidationError('Unexpected extra cell in '+n)
                writer.writerow([str(v) for v in row[:len(header)]]+['']*max(0,len(header)-len(row)))
            z.writestr(n,out.getvalue().encode('utf-8-sig'))
    return buf.getvalue()


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('xlsx');ap.add_argument('--output',required=True)
    args=ap.parse_args(argv)
    out=Path(args.output)
    if out.exists():
        print('ERROR: refusing to overwrite existing output',file=sys.stderr);return 2
    try:
        raw=convert_xlsx_bytes(args.xlsx)
        out.write_bytes(raw)
        try:
            report=validate(out)
        except Exception:
            out.unlink(missing_ok=True);raise
        print(json.dumps({'ok':True,'output':str(out),'package_id':report['package_id'],
                          'kind':report['kind'],'questions':report['question_count'],
                          'safe_to_publish':False,'warnings_count':len(report['warnings'])},ensure_ascii=False,indent=2))
        return 0
    except (ValidationError,ValueError,KeyError,OSError,zipfile.BadZipFile,ET.ParseError) as e:
        print('Excel conversion rejected: '+str(e),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
