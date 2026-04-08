from django.urls import path

from .views import (
    builder_dashboard_page,
    builder_editor_page,
    builder_reset_page,
    builder_save_draft_page,
    builder_state_page,
    builder_variables_page,
    clone_template_page,
    create_template_page,
    dashboard_page,
    editor_page,
    moderation_queue_page,
    moderate_version_page,
    preview_fragment_page,
    render_template_page,
    rollback_template_page,
    template_detail_page,
    upload_asset_page,
    version_history_page,
)

app_name = "web_constructor"

urlpatterns = [
    path("builder/", builder_dashboard_page, name="builder"),
    path("builder/pages/<int:page_id>/", builder_editor_page, name="builder_editor"),
    path("builder/pages/<int:page_id>/state/", builder_state_page, name="builder_state"),
    path("builder/pages/<int:page_id>/save/", builder_save_draft_page, name="builder_save_draft"),
    path("builder/pages/<int:page_id>/reset/", builder_reset_page, name="builder_reset"),
    path("builder/variables/", builder_variables_page, name="builder_variables"),
    path("", dashboard_page, name="dashboard"),
    path("create/", create_template_page, name="create"),
    path("moderation/", moderation_queue_page, name="moderation"),
    path("moderation/version/<int:version_id>/", moderate_version_page, name="moderate_version"),
    path("render/<uuid:template_uuid>/", render_template_page, name="render"),
    path("templates/<uuid:template_uuid>/", template_detail_page, name="detail"),
    path("templates/<uuid:template_uuid>/editor/", editor_page, name="editor"),
    path("templates/<uuid:template_uuid>/preview-fragment/", preview_fragment_page, name="preview_fragment"),
    path("templates/<uuid:template_uuid>/versions/", version_history_page, name="versions"),
    path("templates/<uuid:template_uuid>/clone/", clone_template_page, name="clone"),
    path("templates/<uuid:template_uuid>/assets/upload/", upload_asset_page, name="upload_asset"),
    path("templates/<uuid:template_uuid>/rollback/<int:version_id>/", rollback_template_page, name="rollback"),
]
