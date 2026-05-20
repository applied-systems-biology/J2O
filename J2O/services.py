import os
import contextlib
import fnmatch
import io
import shutil
import mimetypes
from pathlib import Path
import io

from JIPipePlugin import settings

import omero
from omero.rtypes import rstring, rlong
from omero.cli import CLI, NonZeroReturnCode
import omero.model as omodel
from omero.model import ProjectI
from omero.gateway import BlitzGateway

# Directory where JIPipe files are stored (customizable via Django settings)
LOG_DIR = settings.J2O_LOG_DIR
JIPIPE_TEMP_DIR = settings.J2O_TEMP_DIR

def remove_temp_directories(dirs):
    """
    Delete temp directories, but ONLY if they are within JIPIPE_TEMP_DIR.

    Expects list of directory paths.

    Security:
    - only allows directories within JIPIPE_TEMP_DIR
    - refuses deleting JIPIPE_TEMP_DIR itself
    """
    try:

        if not isinstance(dirs, list):
            raise Exception("Unable to remove temp directories. Input must be a list of paths!")

        allowed_base = Path(JIPIPE_TEMP_DIR).resolve()
        if not allowed_base.exists() or not allowed_base.is_dir():
            raise Exception(f"Server misconfiguration. JIPIPE_TEMP_DIR invalid -> '{JIPIPE_TEMP_DIR}' does not exist or is not a directory")

        deleted, skipped, errors = [], [], []

        for raw in dirs:
            try:
                if raw is None or str(raw).strip() == "":
                    skipped.append({"path": raw, "reason": "empty path"})
                    continue

                requested = Path(str(raw))

                # If relative, interpret relative to allowed base
                if not requested.is_absolute():
                    requested = allowed_base / requested

                # Resolve to collapse '..' and symlinks
                requested = requested.resolve()

                # Ensure requested path is inside allowed_base
                try:
                    is_allowed = requested.is_relative_to(allowed_base)
                except AttributeError:
                    # Python < 3.9 fallback
                    is_allowed = (
                        str(requested).startswith(str(allowed_base) + os.sep)
                        or requested == allowed_base
                    )

                if not is_allowed:
                    skipped.append({"path": raw, "reason": "path not allowed"})
                    continue

                # Refuse deleting the base temp dir itself
                if requested == allowed_base:
                    skipped.append({"path": raw, "reason": "refusing to delete base temp dir"})
                    continue

                if requested.is_dir():
                    shutil.rmtree(str(requested))
                    deleted.append(str(requested))
                else:
                    skipped.append({"path": raw, "reason": "not a directory"})

            except Exception as e:
                errors.append({"path": raw, "error": str(e)})

        if errors or skipped:
            raise Exception(f"Failed to remove all temp directories.\nErrors: {errors}\nSkipped: {skipped}")

        return deleted

    except Exception as e:
        raise Exception(str(e))


def get_subdirectories(path, **kwargs):
    """
    Return immediate subdirectories of a given directory path as a list.

    Expects a path.

    Security:
    - only allows paths within JIPIPE_TEMP_DIR
    - lists only immediate subdirectories (no recursion)
    """
    try:
        raw_path = path
        if not raw_path:
            raise Exception("No input was given to get_subdirectories()")

        allowed_base = Path(JIPIPE_TEMP_DIR).resolve()
        if not allowed_base.exists() or not allowed_base.is_dir():
            raise Exception(f"Server misconfiguration. JIPIPE_TEMP_DIR invalid -> '{JIPIPE_TEMP_DIR}' does not exist or is not a directory")

        requested = Path(raw_path)

        # If temp_output is relative, interpret relative to allowed base
        if not requested.is_absolute():
            requested = allowed_base / requested

        # Resolve to remove '..' etc.
        requested = requested.resolve()

        # Ensure requested path is inside allowed_base
        try:
            is_allowed = requested.is_relative_to(allowed_base)
        except AttributeError:
            # Python < 3.9 fallback
            is_allowed = str(requested).startswith(str(allowed_base) + os.sep) or requested == allowed_base

        if not is_allowed:
            raise Exception("Path given to get_subdirectories() not in allowed base path")

        if not requested.exists() or not requested.is_dir():
            raise Exception("Path given to get_subdirectories() not found or not a directory")

        # List immediate subdirectories
        subdirs = []
        for name in os.listdir(str(requested)):
            full_path = requested / name
            if full_path.is_dir():
                subdirs.append(name)

        subdirs.sort()
        return subdirs

    except Exception as e:
        raise Exception(str(e))


def save_to_omero(host, port, session_uuid, root_dir, log_path, project_id, dataset_name, recursive=True, patterns=None):
    """
    - host: Server address running OMERO.web
    - port: Port for the server
    - session_uuid: Session UUID used to connect to OMERO session on server
    - root_dir: Path to directory whose content will be saved to OMERO
    - log_path: Path to log files that will be uploaded to OMERO
    - project_id: OMERO project ID where data should be saved to.
    - dataset_name: Name of the dataset that is saved to OMERO project.
    - recursive: Bool. Determines wether all files or just files directly in root_dir are saved.
    - patterns: Regex. Allows filtering for certain filename patterns.

    Behavior:
      - Creates (or reuses) Dataset in Project
      - Imports files as Images into that Dataset using embedded CLI bound to current conn
      - No session ID string and no username/password required
      - Uses per-call Ice context to set the group (works across OMERO versions)
    """
    DEFAULT_PROJECT_NAME = "JipipeResultsDefault"
    try:

        # Connect to OMERO session
        conn = BlitzGateway(host=host, port=port)
        ok = conn.connect(sUuid=session_uuid)

        # Raise connection error if session can't be reached
        if not ok:
            raise ConnectionError("Unable to connect to OMERO session. Files will NOT be saved to OMERO!")

        # Raise error if directory that should contain results does not exist
        if not os.path.isdir(root_dir):
            raise FileNotFoundError(f"Path not found or not a directory: {root_dir}")
        
        # Ensure project exists
        project = conn.getObject("Project", project_id)
        if project is None:
            # Try to find an existing default project by name (in current group context)
            default_project = next(conn.getObjects("Project", attributes={'name': DEFAULT_PROJECT_NAME}), None)

            if default_project is None:
                # Create a new default project
                pr = ProjectI()
                pr.setName(rstring(DEFAULT_PROJECT_NAME))
                pr = conn.getUpdateService().saveAndReturnObject(pr)  # saved in current group/security context
                project = conn.getObject("Project", pr.id.val)        # wrap as gateway object
            else:
                project = default_project
        
            # From here on, use this project
            project_id = project.getId()

        # ---- Per-call Ice context: force saves into the project's group ----
        gid = project.getDetails().group.id.val
        ctx = {"omero.group": str(gid)}  # <- key bit; no SecurityContext/ServiceOptions needed
        u = conn.c.sf.getUpdateService()  # raw proxy so we can pass _ctx explicitly
        # -------------------------------------------------------------------

        # Ensure (or create) dataset in project using per-call context
        ds_obj = None
        for d in project.listChildren():
            if d.getName() == dataset_name:
                ds_obj = d
                break
        if ds_obj is None:
            ds_m = omodel.DatasetI()
            ds_m.setName(rstring(dataset_name))
            ds_m.setDescription(rstring(f"Bulk import from {root_dir}"))
            ds_m = u.saveAndReturnObject(ds_m, _ctx=ctx)

            link = omodel.ProjectDatasetLinkI()
            link.setParent(project._obj)
            link.setChild(ds_m)
            u.saveAndReturnObject(link, _ctx=ctx)

            dataset_id = ds_m.id.val
        else:
            dataset_id = ds_obj.getId()


        # ---- Attach log.txt to the Dataset using ONLY raw services + the same ctx ----
        attached = []

        if log_path and os.path.isfile(log_path):
            basename = os.path.basename(log_path)

            # 0) Check if a FileAnnotation with the same OriginalFile name is already linked to the Dataset
            q = conn.getQueryService()
            params = omero.sys.ParametersI()
            params.addId(dataset_id)
            params.addString("fname", basename)

            hql = """
                select fa
                from DatasetAnnotationLink dal
                join dal.child fa
                join fa.file f
                where dal.parent.id = :id
                and f.name = :fname
            """

            existing = q.findAllByQuery(hql, params, _ctx=ctx)
            if existing and len(existing) > 0:
                # A file with the same name is already attached to this Dataset; do nothing.
                # (If you prefer, you could log/print a message here.)
                pass
            else:
                # 1) Create an OriginalFile in the target group
                of = omodel.OriginalFileI()
                of.setName(rstring(os.path.basename(log_path)))
                of.setPath(rstring("/"))  # logical server-side path; avoid local absolute paths here
                of.setSize(rlong(os.path.getsize(log_path)))
                of.setMimetype(rstring("text/plain"))
                of = u.saveAndReturnObject(of, _ctx=ctx)  # IMPORTANT: saved with proper group

                # 2) Upload bytes via RawFileStore (note: use createRawFileStore)
                rfs = conn.c.sf.createRawFileStore()
                try:
                    rfs.setFileId(of.getId().getValue(), _ctx=ctx)
                    with open(log_path, "rb") as fh:
                        offset = 0
                        while True:
                            chunk = fh.read(64 * 1024)
                            if not chunk:
                                break
                            rfs.write(chunk, offset, len(chunk), _ctx=ctx)
                            offset += len(chunk)
                    rfs.save(_ctx=ctx)
                finally:
                    rfs.close()

                # 3) Create FileAnnotation referencing the OriginalFile
                fa = omodel.FileAnnotationI()
                fa.setFile(omodel.OriginalFileI(of.getId().getValue(), False))
                fa.setNs(rstring("omero.jipipe/log"))  # optional namespace
                fa = u.saveAndReturnObject(fa, _ctx=ctx)

                # 4) Link FileAnnotation -> Dataset
                dal = omodel.DatasetAnnotationLinkI()
                dal.setParent(omodel.DatasetI(dataset_id, False))
                dal.setChild(omodel.FileAnnotationI(fa.getId().getValue(), False))
                u.saveAndReturnObject(dal, _ctx=ctx)
                attached.append({"file": log_path, "type": "attachment"})
            # -----------------------------------------------------------------------------

        # Gather candidate files
        candidates = _gather_files(root_dir, recursive=recursive, patterns=patterns)
        if not candidates:
            # Still return success (no error)
            return {
                "ok": True,
                "nothing_to_upload": True,
                "message": "No matching files found; nothing uploaded.",
                "project_id": project_id,
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
                "root_dir": root_dir,
                "recursive": recursive,
                "patterns": patterns,
                "files_considered": 0,
                "files_imported": 0,
                "files_attached": len(attached),   # log attachment count if any
                "files_skipped": 0,
                "results": attached,               # include log attachment results if you made them
                "errors": [],
                "stdout": "",
                "stderr": "",
            }
        
        # Decide what we consider importable image files
        IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".dv", ".czi", ".nd2", ".lif", ".ics", ".ids", ".svs", ".mrxs"}
        files_for_import = [p for p in candidates if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
        files_for_attach = [p for p in candidates if p not in files_for_import]

        results, errors = [], []

        if files_for_attach:
            for apath in files_for_attach:
                try:
                    of = omodel.OriginalFileI()
                    of.setName(rstring(os.path.basename(apath)))
                    of.setPath(rstring("/"))
                    of.setSize(rlong(os.path.getsize(apath)))
                    mt, _ = mimetypes.guess_type(apath)
                    of.setMimetype(rstring(mt or "application/octet-stream"))
                    of = u.saveAndReturnObject(of, _ctx=ctx)

                    rfs = conn.c.sf.createRawFileStore()
                    try:
                        rfs.setFileId(of.getId().getValue(), _ctx=ctx)
                        with open(apath, "rb") as fh:
                            offset = 0
                            while True:
                                chunk = fh.read(64 * 1024)
                                if not chunk:
                                    break
                                rfs.write(chunk, offset, len(chunk), _ctx=ctx)
                                offset += len(chunk)
                        rfs.save(_ctx=ctx)
                    finally:
                        rfs.close()

                    fa = omodel.FileAnnotationI()
                    fa.setFile(omodel.OriginalFileI(of.getId().getValue(), False))
                    fa.setNs(rstring("omero.jipipe/attachment"))
                    fa = u.saveAndReturnObject(fa, _ctx=ctx)

                    dal = omodel.DatasetAnnotationLinkI()
                    dal.setParent(omodel.DatasetI(dataset_id, False))
                    dal.setChild(omodel.FileAnnotationI(fa.getId().getValue(), False))
                    u.saveAndReturnObject(dal, _ctx=ctx)

                    attached.append({"file": apath, "type": "attachment"})
                except Exception as e:
                    errors.append({"file": apath, "error": f"attach failed: {e}"})

        imported = 0

        if files_for_import:
            base_args = [
                "import",
                "-g", str(gid),
                "-T", f"Dataset:{dataset_id}",
                "--no-upgrade-check",
            ]
            full_args = base_args + files_for_import

            buf_out, buf_err = io.StringIO(), io.StringIO()
            try:
                cli = CLI()
                cli.loadplugins()
                cli.set_client(conn.c)
                with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                    cli.invoke(full_args, strict=True)

                for fpath in files_for_import:
                    results.append({"file": fpath, "type": "image"})
                    imported += 1

            except NonZeroReturnCode as nz:
                # Surface BOTH streams so we don't lose the real message
                err_text = "\n".join([buf_err.getvalue().strip(), buf_out.getvalue().strip()]).strip()
                return {
                    "error": err_text or f"OMERO import failed (exit code {getattr(nz, 'returncode', '?')})",
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "root_dir": root_dir,
                    "recursive": recursive,
                    "patterns": patterns,
                    "files_for_import": files_for_import,
                    "files_for_attach": files_for_attach,
                }
            except Exception as e:
                raise Exception(str(e))
        else:
            # No images to import; that's fine if we attached something
            pass

        return {
            "project_id": project_id,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "root_dir": root_dir,
            "recursive": recursive,
            "patterns": patterns,
            "files_considered": len(candidates),
            "files_imported": imported,
            "files_attached": len(attached),
            "files_skipped": len(candidates) - imported - len(attached),
            "results": results + attached,
            "errors": errors,
            "stdout": (buf_out.getvalue().strip() if files_for_import else ""),
            "stderr": (buf_err.getvalue().strip() if files_for_import else ""),
        }

    except Exception as e:
        raise Exception(str(e))

def _gather_files(root_dir, recursive=True, patterns=None):
    try:
        if not recursive:
            files = [os.path.join(root_dir, f) for f in os.listdir(root_dir)
                    if os.path.isfile(os.path.join(root_dir, f))]
            if patterns:
                files = [p for p in files
                        if any(fnmatch.fnmatch(os.path.basename(p), pat) for pat in patterns)]
            return list(dict.fromkeys(files))
        out = []
        for base, _, fs in os.walk(root_dir):
            for f in fs:
                if not patterns or any(fnmatch.fnmatch(f, pat) for pat in patterns):
                    out.append(os.path.join(base, f))
        return list(dict.fromkeys(out))
    except Exception as e:
        raise Exception(f"Failed to gather candidate files to sav to OMERO: {str(e)}")