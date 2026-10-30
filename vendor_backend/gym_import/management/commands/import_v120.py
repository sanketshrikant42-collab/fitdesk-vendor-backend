"""python manage.py import_v120 "C:\\ProgramData\\FitDesk Pro\\fitdesk.db" [--replace] [--report out.html]

Copies one gym from a FitDesk v1.2.0 database into a NEW gym on this
server, checks every count and rupee, and writes an HTML check report.
The v1.2.0 file is only read. Run it again with --replace to throw away
the previous import of the same gym and import fresh (only a gym created
by this command can be replaced)."""
import os
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from gym_import import reconcile as RC
from gym_import import report as RP
from gym_import.importer import ImportProblem, Importer, IST, find_previous_import
from gym_import.source import Source, SourceError, snapshot


class Command(BaseCommand):
    help = "Import one gym from a FitDesk v1.2.0 database (fitdesk.db) into a new gym, with a check report."

    def add_arguments(self, p):
        p.add_argument("source", help="Path to v1.2.0 fitdesk.db (or a copy of it).")
        p.add_argument("--gym-name", help="Name for the new gym (default: the name in v1.2.0 settings).")
        p.add_argument("--owner-name", default="")
        p.add_argument("--owner-phone", default="")
        p.add_argument("--replace", action="store_true", help="Delete the previous import of this gym first.")
        p.add_argument("--dry-run", action="store_true", help="Import, check and report, then undo everything.")
        p.add_argument("--report", help="Where to save the HTML check report (default: next to the source file).")
        p.add_argument("--today", help="Check as of this date (YYYY-MM-DD); default today in India.")

    def handle(self, *a, **o):
        try:
            tmp, sha = snapshot(o["source"])
        except SourceError as e:
            raise CommandError(str(e))
        try:
            src = Source(tmp)
        except SourceError as e:
            os.remove(tmp)
            raise CommandError(str(e))
        today = date.fromisoformat(o["today"]) if o.get("today") else timezone.localdate(timezone=IST)
        imp = Importer(src, gym_name=o.get("gym_name"), owner_name=o["owner_name"], owner_phone=o["owner_phone"], today=today)
        prev = find_previous_import(source_gym_name=src.settings()["gym_name"])
        if prev and not o["replace"] and not o["dry_run"]:
            raise CommandError(f"This gym was already imported as “{prev.client.gym_name}” (gym id {prev.client_id}) on "
                               f"{prev.created_at:%d %b %Y %H:%M}. Add --replace to delete that import and do it again.")
        try:
            with transaction.atomic():
                imp.run(replace_client=prev.client if (prev and o["replace"]) else None)
                check = RC.reconcile(src, imp, today)
                html = RP.render(imp, check, RC.not_moved(src), o["source"], sha, today)
                summary = {"counts": imp.counts, "problems": len(check.problems), "changes": len(imp.changes),
                           "cleanup": len(imp.cleanup), "dry_run": o["dry_run"]}
                if o["dry_run"]:
                    transaction.set_rollback(True)
                else:
                    imp.record(o["source"], sha, ok=True, summary=summary)
        except ImportProblem as e:
            raise CommandError(f"Import stopped, nothing was saved: {e}")
        finally:
            src.close()
            try:
                os.remove(tmp)
            except OSError:
                pass
        out = o.get("report") or os.path.join(os.path.dirname(os.path.abspath(o["source"])),
                                              f"import_check_{today:%Y-%m-%d}.html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        w = self.stdout.write
        w(self.style.SUCCESS(f"{'DRY RUN (nothing kept): ' if o['dry_run'] else ''}Imported “{imp.gym_name}”"
                             + ("" if o["dry_run"] else f" as gym id {imp.client.id}")))
        for k, v in imp.counts.items():
            w(f"  {k}: {v}")
        w((self.style.SUCCESS("All checks match.") if not check.problems
           else self.style.ERROR(f"{len(check.problems)} difference(s): see the report.")))
        w(f"Changed on the way in: {len(imp.changes)} · To clean up: {len(imp.cleanup)}")
        if not o["dry_run"]:
            w(f"Licence key: {imp.licence.key}")
            w(f"Owner setup code: {imp.setup_code}  (use it on /api/v1/test-console/ to create the owner login)")
        w(f"Report: {out}")
