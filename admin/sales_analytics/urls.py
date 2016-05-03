from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^dashboard$', views.dashboard, name='dashboard'),
    url(r'^user_session', views.user_session, name='user_session'),
    url(r'^products_view', views.products_view, name='products_view'),
    url(r'^products_usage', views.products_usage, name='products_usage'),
    url(r'^debug_test', views.debug_test, name='debug_test'),
]
