# FitDesk test app (local only)

One page for everything the gym does, in place of the separate Phase 3A and 3B test pages.
It only exists when the server runs with `DEBUG=True`.

| Page | Address |
|---|---|
| Sign-in, team & devices (Phase 2) | `http://127.0.0.1:8000/api/v1/test-console/` |
| Gym app | `http://127.0.0.1:8000/api/v1/test-console/app/` |

The old addresses (`test-console/gym/` and `test-console/ops/`) now open the gym app.

## What's in it

- **One menu:** Front desk, Members, Money, Team, Gym.
- **Light and dark mode** with the button at the top. It's remembered on that browser, and it starts with the computer's own setting.
- **Member search everywhere:** wherever a member is needed (new bill, family/couple plans, diet, progress, consent, "referred by"), type a name, mobile or code and pick from the list.
- **Add member in one go:**
  1. Basic details (saved straight away).
  2. Personal details.
  3. Fitness profile.
  4. Done, with a choice of what's next: start membership, sign the consent form, open the profile, or add another member.

  Steps 2 and 3 can be skipped and filled in later from the profile.
- **Forms open in a pop-up window** ("+ Add …") instead of sitting beside the lists.
- **Profiles** for members, staff and trainers open from any list.

## Staff and trainers

- Gym employees who train members are added in **Staff** with the department **Trainer**. They appear under **Trainers** automatically.
- If their department changes or they leave, they stop showing as trainers.
- **Add freelance trainer** is only for trainers who aren't on payroll.
- A trainer can be removed from their profile (Edit → Remove trainer). A trainer who was never used is deleted. One with bills is marked inactive instead, so old receipts keep their name.

### Clean-up of stray trainers

Before this fix, a failed **Add staff** (for example, a mobile number already used) could still create a trainer. To list such stray records:

```
python manage.py fix_orphan_trainers
```

To remove them:

```
python manage.py fix_orphan_trainers --apply
```

Only trainers that were never used and share a mobile number with a non-trainer staff member are touched.
