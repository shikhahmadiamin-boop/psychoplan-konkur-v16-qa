"""Limited-size temporary upload, strict offline validation and no silent writes."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile
from .importer_validation import MAX_ZIP_BYTES, validate, ValidationError
from .xlsx_bridge import MAX_XLSX, convert_xlsx_bytes
from .catalog_builder import build_official_preview


@contextmanager
def prepare_package(uploaded):
    original = Path(uploaded.name).suffix.lower()
    if original not in {'.zip','.xlsx'}:
        raise ValidationError('Only ZIP or XLSX is accepted')
    limit = MAX_ZIP_BYTES if original=='.zip' else MAX_XLSX
    if uploaded.size is not None and uploaded.size > limit:
        raise ValidationError('File exceeds the safe size limit')
    with TemporaryDirectory(prefix='pp_konkur_upload_') as folder:
        base = Path(folder)
        upload = base/('upload'+original)
        size=0
        with upload.open('xb') as f:
            for chunk in uploaded.chunks(chunk_size=512*1024):
                size+=len(chunk)
                if size>limit:
                    raise ValidationError('File exceeds the safe size limit')
                f.write(chunk)
        if size==0:
            raise ValidationError('Empty file')
        if original=='.xlsx':
            result=convert_xlsx_bytes(upload)
            if len(result)>MAX_ZIP_BYTES:
                raise ValidationError('Converted ZIP exceeds the safe size limit')
            dest=base/'validated.zip'
            dest.write_bytes(result)
        else:
            dest=upload
        yield dest


def preview_package(path, expected_kind, window=12):
    report=validate(path)
    if report['kind']!=expected_kind:
        raise ValidationError('This upload belongs to the other admin section')
    summary={key:report[key] for key in ('package_id','kind','digest','question_count','warnings','safe_to_publish')}
    if expected_kind=='OFFICIAL_YEARLY':
        summary['automatic_modes']=build_official_preview(report['records'],window=window)
    else:
        rows=report['records']
        summary['families']=len(rows['families.csv'])
        summary['concepts']=len(rows['concepts.csv'])
        summary['explanations']=len(rows['explanations.csv'])
    return summary
