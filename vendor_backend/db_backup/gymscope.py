"""Which rows belong to one gym, in a backup or in the live database.

A row belongs to a gym if it points at the gym (licensing.Client), or at
another row that belongs to it (a payment -> its bill -> the gym). Only the
new system's tables are included; the gym itself, its licence, desktop
devices and desktop backups (licensing / cloud_backup) are never touched.
"""
from graphlib import TopologicalSorter

from django.apps import apps
from django.db import transaction

from .core import BackupError, _check_migrations, load

GYM_APPS = ["gym_accounts", "gym_core", "gym_ops", "gym_auto", "gym_import"]
NOT_RESTORED = {"gym_accounts.staffsession", "gym_accounts.authattempt"}   # everyone signs in again after a restore
CLIENT = "licensing.client"


def gym_models():
    out = []
    for a in GYM_APPS:
        for m in apps.get_app_config(a).get_models():
            if m._meta.label_lower == "gym_accounts.authattempt":
                continue
            if any(f.many_to_many and f.concrete for f in m._meta.get_fields()):
                raise BackupError(f"{m._meta.label} has a many-to-many field; gymscope needs updating.")
            out.append(m)
    return out


def links(model):
    """[(field name, field attname, target label)] for every foreign key."""
    return [(f.name, f.attname, f.related_model._meta.label_lower)
            for f in model._meta.get_fields() if f.concrete and (f.many_to_one or f.one_to_one)]


def in_backup(data, client_id):
    """Rows of the backup that belong to this gym."""
    models = {m._meta.label_lower: links(m) for m in gym_models()}
    rows = [o for o in data if o["model"] in models]
    owned = {CLIENT: {str(client_id)}}
    changed = True
    while changed:
        changed = False
        for o in rows:
            have = owned.setdefault(o["model"], set())
            pk = str(o["pk"])
            if pk in have:
                continue
            for name, _, tgt in models[o["model"]]:
                v = o["fields"].get(name)
                if v is not None and str(v) in owned.get(tgt, ()):
                    have.add(pk)
                    changed = True
                    break
    return [o for o in rows if str(o["pk"]) in owned.get(o["model"], ())]


def in_live(client_id):
    """{model: set of primary keys} of live rows that belong to this gym."""
    models = gym_models()
    owned = {CLIENT: {client_id}}
    changed = True
    while changed:
        changed = False
        for m in models:
            label = m._meta.label_lower
            have = owned.setdefault(label, set())
            for _, att, tgt in links(m):
                ids = owned.get(tgt)
                if not ids:
                    continue
                new = set(m._base_manager.filter(**{f"{att}__in": list(ids)}).values_list("pk", flat=True)) - have
                if new:
                    have |= new
                    changed = True
    return {m: owned.get(m._meta.label_lower, set()) for m in models}


def delete_order(models):
    """Children before parents."""
    by = {m._meta.label_lower: m for m in models}
    ts = TopologicalSorter()
    for m in models:
        ts.add(m._meta.label_lower)
        for _, _, tgt in links(m):
            if tgt in by and tgt != m._meta.label_lower:
                ts.add(tgt, m._meta.label_lower)      # tgt must be deleted after m
    return [by[x] for x in ts.static_order()]


def counts_live(client_id):
    return {m._meta.label_lower: len(pks) for m, pks in in_live(client_id).items() if pks}


def counts_rows(rows):
    out = {}
    for o in rows:
        out[o["model"]] = out.get(o["model"], 0) + 1
    return out


def restore_gym(manifest, data, client_id):
    """Replaces this gym's data in the live database with the backup's.
    Other gyms are not touched. All or nothing."""
    from licensing.models import Client
    if not Client.objects.filter(pk=client_id).exists():
        raise BackupError("That gym doesn't exist on this server any more. Restore the whole backup into a new "
                          "database and copy it from there.")
    if str(client_id) not in {str(g["id"]) for g in manifest.get("gyms", [])}:
        raise BackupError("That gym isn't in this backup.")
    _check_migrations(manifest)
    rows = [o for o in in_backup(data, client_id) if o["model"] not in NOT_RESTORED]
    with transaction.atomic():
        live = in_live(client_id)
        for m in delete_order(list(live)):
            pks = live[m]
            if pks:
                qs = m._base_manager.filter(pk__in=list(pks))
                qs._raw_delete(qs.db)
        load(rows)
    return counts_rows(rows)
