from django.urls import path
from .views import UserCreateView, CustomTokenObtainPairView, LogoutView, UtilisateurListView

urlpatterns = [
    path('add/', UserCreateView.as_view(), name='user-create'),
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='login'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('listuser/', UtilisateurListView.as_view(), name='utilisateur-list')
]
