# Generated by Django 5.1.3 on 2024-12-09 17:12

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Utilisateur',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('username', models.CharField(max_length=100, unique=True)),
                ('role', models.CharField(choices=[('Admin', 'Admin'), ('Scientifique', 'Scientifique'), ('Médecin', 'Médecin')], max_length=20)),
                ('password_hash', models.TextField()),
            ],
            options={
                'abstract': False,
            },
        ),
    ]