from django.db import connection
from django.db.utils import Error as DatabaseError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class SaudeView(APIView):
    """Verifica que a API responde e que o banco de dados esta acessivel."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except DatabaseError:
            return Response(
                {"status": "erro"}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response({"status": "ok"})
