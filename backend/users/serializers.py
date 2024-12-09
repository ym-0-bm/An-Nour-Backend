from rest_framework import serializers
from .models import Utilisateur


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})

    class Meta:
        model = Utilisateur
        fields = ['id', 'username', 'role', 'password']

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = Utilisateur.objects.create_user(**validated_data, password=password)
        return user


class UtilisateurSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['id', 'username', 'role']
