from django.urls import path

from . import views

urlpatterns = [path("data-export/", views.export_all)]
