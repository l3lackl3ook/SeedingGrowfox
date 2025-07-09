"""
URL configuration for FB_WebApp_Project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from PageInfo import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.index, name='index'),
    path('create-group/', views.create_group, name='create_group'),  # ✅ เพิ่มบรรทัดนี้
    path('add-page/<int:group_id>/', views.add_page, name='add_page'),
    path('group/<int:group_id>/', views.group_detail, name='group_detail'),
    path('page/<int:page_id>/', views.pageview, name='pageview'),
    path('add-comment-url/', views.add_comment_url, name='add_comment_url'),
    path("comment-dashboard/", views.comment_dashboard_view, name="comment_dashboard"),
    path('add-activity-dashboard/', views.add_activity_dashboard, name='add_activity_dashboard'),
    path('edit-comment/<int:comment_id>/', views.edit_comment, name='edit_comment'),
]

# ✅ เพิ่มตรงนี้เพื่อให้ static files โหลดได้ตอน DEBUG = True
from django.conf import settings
from django.conf.urls.static import static
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)