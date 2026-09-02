from django.urls import path

from . import views


urlpatterns = [

    # Existing URLs
    path(
        "validate-license/",
        views.validate_license,
        name="validate_license"
    ),

    path(
        "submit-query/",
        views.submit_query,
        name="submit_query"
    ),

    # NEW
    path(
        "support-queries/",
        views.get_support_queries,
        name="get_support_queries"
    ),
]