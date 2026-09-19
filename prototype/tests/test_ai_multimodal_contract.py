from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EDGE = ROOT / "prototype" / "supabase" / "functions" / "watchlog-ai"
PORTAL = ROOT / "portal" / "app"
MIG = ROOT / "prototype" / "supabase" / "migrations" / "0111_ai_chat_multimodal_admin.sql"

def test_edge_accepts_customer_images_and_keeps_them_private():
    src = (EDGE / "index.ts").read_text(encoding="utf-8")
    assert 'CHAT_IMAGE_BUCKET = "ai-chat-attachments"' in src
    assert 'p_needs_vision: hasImages' in src
    assert 'body?.op === "attachment_url"' in src
    assert '"customer_image"' in src
    assert "createSignedUrl" in src

def test_provider_messages_support_openai_multimodal_content():
    src = (EDGE / "providers" / "types.ts").read_text(encoding="utf-8")
    assert 'type: "image_url"' in src
    assert "ChatImagePart" in src

def test_private_attachment_schema_and_admin_reader_exist():
    src = MIG.read_text(encoding="utf-8")
    assert "ai_message_attachments" in src
    assert "'ai-chat-attachments'" in src
    assert "public=false" in src
    assert "wl_platform_ai_conversations" in src
    assert "wl_platform_ai_conversation" in src

def test_customer_and_admin_ui_expose_images_and_conversations():
    composer = (PORTAL / "ai" / "customer-composer.js").read_text(encoding="utf-8")
    hook = (PORTAL / "ai" / "use-customer-chat.js").read_text(encoding="utf-8")
    admin = (PORTAL / "admin" / "ai" / "conversations" / "page.js").read_text(encoding="utf-8")
    assert 'accept="image/jpeg,image/png,image/webp"' in composer
    assert "MAX_IMAGES=3" in hook
    assert "wl_platform_ai_conversations" in admin
    assert "wl_platform_ai_conversation" in admin
