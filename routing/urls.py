from django.urls import path

from .views import RoutePlanView, map_view

urlpatterns = [
    path("route/", RoutePlanView.as_view()),
    path("map/", map_view),
]