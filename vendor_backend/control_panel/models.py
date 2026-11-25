"""Crestline's own records, used only by the control panel (/control/).

GymBilling    what a gym pays Crestline per year (and whether it's on the launch offer)
VendorPayment one payment a gym made to Crestline, with its invoice number
SupportMeta   Crestline's private side of a support question (note, call-back flag)

None of these are read by the gym apps or the desktop licence check.
"""
from django.db import models
from django.utils import timezone

from licensing.models import Client, License, SupportQuery

LAUNCH_OFFER_SEATS = 50
LAUNCH_OFFER_PRICE = 18000
REGULAR_PRICE = 25000


class GymBilling(models.Model):
    client = models.OneToOneField(Client, related_name="billing", on_delete=models.CASCADE)
    yearly_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    launch_offer = models.BooleanField(default=False, help_text="One of the first 50 gyms at the launch price.")
    gstin = models.CharField(max_length=20, blank=True, help_text="The gym's GSTIN, printed on Crestline's invoice.")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def for_client(cls, client):
        obj, _ = cls.objects.get_or_create(client=client)
        return obj


class VendorPayment(models.Model):
    MODES = ["UPI", "Cash", "Bank transfer", "Cheque"]

    client = models.ForeignKey(Client, related_name="vendor_payments", on_delete=models.PROTECT)
    license = models.ForeignKey(License, null=True, blank=True, related_name="vendor_payments", on_delete=models.SET_NULL)
    invoice_no = models.CharField(max_length=30, unique=True)
    paid_on = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    mode = models.CharField(max_length=20)
    reference = models.CharField(max_length=60, blank=True)
    months = models.PositiveIntegerField(default=0, help_text="How long the licence was extended by (0 = not extended).")
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    plan_name = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-paid_on", "-created_at"]

    def __str__(self):
        return f"{self.invoice_no} · {self.client.gym_name} · {self.amount}"


class SupportMeta(models.Model):
    query = models.OneToOneField(SupportQuery, related_name="crestline", on_delete=models.CASCADE)
    call_back = models.BooleanField(default=False)
    private_note = models.TextField(blank=True)
    last_reply_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def for_query(cls, q):
        obj, _ = cls.objects.get_or_create(query=q)
        return obj
