from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models


class CustomUserManager(BaseUserManager):
    def create_user(self, username, password=None, role=None):
        if not username:
            raise ValueError("Le nom d'utilisateur est obligatoire.")
        if not role:
            raise ValueError("Le rôle est obligatoire.")
        user = self.model(username=username, role=role)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None):
        return self.create_user(username=username, password=password, role='Admin')


class Utilisateur(AbstractBaseUser):
    ROLES = [
        ('Admin', 'Admin'),
        ('Scientifique', 'Scientifique'),
        ('Médecin', 'Médecin'),
    ]

    username = models.CharField(max_length=100, unique=True)
    role = models.CharField(max_length=20, choices=ROLES)
    password_hash = models.TextField()

    objects = CustomUserManager()

    USERNAME_FIELD = 'username'
