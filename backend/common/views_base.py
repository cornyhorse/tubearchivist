"""base classes to inherit from"""

from typing import Callable

from common.src.index_generic import Pagination
from common.src.search_processor import SearchProcess
from rest_framework import permissions
from rest_framework.authentication import (
    SessionAuthentication,
    TokenAuthentication,
)
from rest_framework.views import APIView


def check_admin(user):
    """check for admin permission for restricted views"""
    return user.is_staff or user.groups.filter(name="admin").exists()


class AdminOnly(permissions.BasePermission):
    """allow only admin"""

    def has_permission(self, request, view):
        return check_admin(request.user)


class AdminWriteOnly(permissions.BasePermission):
    """allow only admin writes"""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return permissions.IsAuthenticated().has_permission(request, view)

        return check_admin(request.user)


class ApiBaseView(APIView):
    """base view to inherit from"""

    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def __init__(self):
        super().__init__()
        self.response = {}
        self.status_code = False
        self.context = False
        self.pagination_handler = False

    def get_document(
        self, index: str, document: dict | None, progress_match=None
    ) -> None:
        """format a single document, 404 if None"""
        if document is None:
            print(f"item not found in {index}")
            self.response = {}
            self.status_code = 404
            return

        self.response = SearchProcess(
            match_video_user_progress=progress_match
        ).process_one(index, document)
        self.status_code = 200

    def get_document_list(
        self,
        request,
        index: str,
        queryset,
        to_doc: Callable = lambda obj: obj.to_doc(),
        pagination: bool = True,
        progress_match=None,
        to_docs: Callable[[list], list[dict]] | None = None,
        page_size: int | None = None,
    ) -> None:
        """
        format a page of queryset, or all of it without pagination
        to_docs: builds the documents of a whole page at once, over to_doc
        page_size: overrides the user's page size
        """
        if pagination:
            self.pagination_handler = Pagination(request, page_size)
            page = self.pagination_handler.pagination
            start = page["page_from"]
            total = queryset.count()
            objects = queryset[start : start + page["page_size"]]
        else:
            objects = queryset

        processor = SearchProcess(match_video_user_progress=progress_match)
        if to_docs:
            documents = to_docs(objects)
        else:
            documents = [to_doc(obj) for obj in objects]
        self.response["data"] = processor.process_many(index, documents)
        self.status_code = 200 if self.response["data"] else 404

        if pagination:
            self.pagination_handler.validate(total)
            self.response["paginate"] = self.pagination_handler.pagination
