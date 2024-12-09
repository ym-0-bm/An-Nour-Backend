from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from .models import Utilisateur
from .serializers import UserSerializer
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.filters import SearchFilter


class UserCreateView(APIView):
    permission_classes = [AllowAny]  # Permet l'accès à tous

    # permission_classes = [IsAdminUser] # Seuls les admins peuvent accéder à cette vue.

    def post(self, request, *args, **kwargs):
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Pour le login, on utilise la vue déjà fournie par SimpleJWT
class CustomTokenObtainPairView(TokenObtainPairView):
    # Vous pouvez personnaliser cette vue si vous le souhaitez
    pass


# Pour le logout, on révoque le jeton d'actualisation
class LogoutView(APIView):
    def post(self, request):
        try:
            # On tente de récupérer le jeton d'actualisation à partir des cookies ou de l'en-tête Authorization
            refresh_token = request.data.get('refresh')
            if not refresh_token:
                return Response({"detail": "Refresh token is required"}, status=status.HTTP_400_BAD_REQUEST)

            # On révoque le jeton
            token = RefreshToken(refresh_token)
            # Assurez-vous que vous avez activé le BlacklistToken en suivant la documentation simplejwt
            token.blacklist()

            return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class UtilisateurListView(generics.ListAPIView):
    queryset = Utilisateur.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser]  # Assurez-vous que seule l'admin peut voir tous les utilisateurs
    filter_backends = [SearchFilter]
    search_fields = ['role']  # Permet de filtrer les utilisateurs par rôle
