import os
import json
import re
import shutil
import asyncio
from datetime import datetime
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv, find_dotenv

import chainlit as cl
import google.generativeai as genai

# Try to import the audio library
try:
    from gtts import gTTS
except ImportError:
    gTTS = None

# --- ROBUST ENV LOADING ---
_ = load_dotenv(find_dotenv(usecwd=True)) 

# --- AUTOMATIC UI CONFIGURATION (Quasar Branding & Audio) ---
def setup_quasar_ui():
    """Automatically configures the Chainlit Sidebar, Logo, Name, and Configs."""
    public_dir = Path("public")
    public_dir.mkdir(exist_ok=True)
    
    logo_path = Path("input_file_0.png")
    if logo_path.exists():
        shutil.copy(logo_path, public_dir / "logo_light.png")
        shutil.copy(logo_path, public_dir / "logo_dark.png")
        shutil.copy(logo_path, public_dir / "favicon.png")
        
    config_path = Path(".chainlit/config.toml")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 1. Rename to Quasar
        content = re.sub(r'name\s*=\s*".*?"', 'name = "Quasar"', content)
        # 2. Ensure Audio is forced ON
        content = re.sub(r'\[features\.audio\]\s*\n\s*enabled\s*=\s*false', '[features.audio]\n    enabled = true', content)
        
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)

    md_path = Path("chainlit.md")
    md_content = """# 👋 Welcome to Quasar
I am your personal AI assistant.

**Quick Controls:**
- 🎙️ **Microphone:** Click the mic icon in the typing box to speak your message!
- 🔊 **Listen:** Click 'Read Aloud' under my messages to hear my voice.
- 📝 **New Chat:** Click the icon at the top right to start a fresh conversation.
- 📜 **Logs:** Click 'View Past Logs' at the bottom of my messages to read your history!
"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

setup_quasar_ui()

# --- CONFIGURATION & PERSISTENCE ---
MEMORY_FILE = Path("ai_memory.json")
LOG_FILE = Path("chat_history.json")

def load_memory():
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except: return {"corrections": []}
    return {"corrections": []}

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=4)

def save_chat_log(entry):
    logs = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r") as f:
                logs = json.load(f)
        except: logs = []
    logs.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=4)

# --- FEATURE 1: CHAT STARTERS ---
@cl.set_starters
async def set_starters():
    return [
        cl.Starter(
            label="👋 Introduce Yourself",
            message="Hi Quasar! Can you introduce yourself and tell me what you can help me with?",
            icon="/public/favicon.png"
        ),
        cl.Starter(
            label="📜 View Chat Logs",
            message="I want to view my past chat history.",
            icon="/public/favicon.png"
        ),
        cl.Starter(
            label="🧠 Explain a Concept",
            message="Explain quantum entanglement as if I were a 5-year-old.",
            icon="/public/favicon.png"
        ),
        cl.Starter(
            label="💻 Generate Code",
            message="Write a Python script for a simple to-do list.",
            icon="/public/favicon.png"
        )
    ]

# --- CHAT HISTORY LOGIC ---
async def display_history():
    logs = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r") as f:
                logs = json.load(f)
        except: pass
            
    if not logs:
        await cl.Message(content="📭 Your chat history is currently empty.").send()
        return
        
    history_text = "### 📜 Recent Chat Logs\n\n"
    for entry in logs[-5:]:
        history_text += f"**🗣️ You:** {entry.get('user')}\n**🤖 Quasar:** {entry.get('ai')}\n*🕒 {entry.get('timestamp')}*\n\n---\n"
        
    await cl.Message(content=history_text).send()

@cl.action_callback("view_history")
async def view_history_callback(action: cl.Action):
    await display_history()


# --- FEATURE 2: TEXT-TO-SPEECH (READ ALOUD) ---
@cl.action_callback("read_aloud")
async def read_aloud_callback(action: cl.Action):
    if gTTS is None:
        await cl.Message(content="⚠️ **Error:** The `gTTS` library is missing. Please run `pip install gTTS`.").send()
        return

    text_to_read = action.payload.get("text", "")
    temp_msg = await cl.Message(content="⏳ *Synthesizing voice...*").send()
    
    clean_text = re.sub(r'[*_`#]', '', text_to_read)
    
    def generate_audio_bytes():
        tts = gTTS(text=clean_text, lang='en', slow=False)
        fp = BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp.read()
    
    try:
        audio_bytes = await asyncio.to_thread(generate_audio_bytes)
        audio_element = cl.Audio(name="Quasar's Voice", content=audio_bytes, display="inline", mime="audio/mpeg")
        
        temp_msg.content = "🔊 **Audio Ready! Click play below:**"
        temp_msg.elements = [audio_element]
        await temp_msg.update()
        
    except Exception as e:
        temp_msg.content = f"⚠️ Failed to generate audio: {str(e)}"
        await temp_msg.update()


# --- FEATURE 3: MICROPHONE (SPEECH-TO-TEXT) HANDLING ---
@cl.on_audio_chunk
async def on_audio_chunk(chunk):
    """Catches the live audio chunks while the user is speaking."""
    buffer = cl.user_session.get("audio_buffer")
    if buffer is None:
        buffer = BytesIO()
        cl.user_session.set("audio_buffer", buffer)
        
        # Determine the MIME type provided by the browser (usually audio/webm)
        mime_type = getattr(chunk, "mimeType", None) or getattr(chunk, "mime_type", "audio/webm")
        cl.user_session.set("audio_mime_type", mime_type)
        
    buffer.write(chunk.data)

@cl.on_audio_end
async def on_audio_end(*args, **kwargs):
    """Activates the moment the user stops recording and releases the mic."""
    buffer: BytesIO = cl.user_session.get("audio_buffer")
    if not buffer:
        return
        
    buffer.seek(0)
    audio_bytes = buffer.read()
    mime_type = cl.user_session.get("audio_mime_type", "audio/webm")
    
    # Clear the buffer for the next time they use the mic
    cl.user_session.set("audio_buffer", None)
    
    # Send a placeholder so the user knows we heard them
    temp_msg = cl.Message(author="User", content="🎙️ *Listening and transcribing...*")
    await temp_msg.send()
    
    try:
        # We use Gemini's native Audio capabilities to transcribe it perfectly!
        model = genai.GenerativeModel("gemini-1.5-flash") 
        response = await model.generate_content_async([
            {"mime_type": mime_type, "data": audio_bytes},
            "You are a highly accurate transcriber. Transcribe this audio exactly as spoken. Just output the text, nothing else."
        ])
        
        transcription = response.text.strip()
        
        if not transcription:
            temp_msg.content = "⚠️ *Could not hear any words clearly. Please try again.*"
            await temp_msg.update()
            return
            
        # 1. Update the placeholder message with their transcribed speech
        temp_msg.content = transcription
        await temp_msg.update()
        
        # 2. Automatically feed their transcribed voice into the main chat loop!
        await main(temp_msg)
        
    except Exception as e:
        temp_msg.content = f"⚠️ *Failed to transcribe audio: {str(e)}*"
        await temp_msg.update()


# --- INITIALIZATION ---
@cl.on_chat_start
async def start():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        await cl.Message(content="❌ **Error:** GOOGLE_API_KEY missing in .env file.").send()
        return

    genai.configure(api_key=api_key)
    
    try:
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if not available_models:
            await cl.Message(content="❌ **Error:** No text generation models available.").send()
            return
            
        preferences = ["models/gemini-1.5-flash", "models/gemini-1.5-pro", "models/gemini-pro"]
        target_model = next((pref for pref in preferences if pref in available_models), available_models[0])
            
    except Exception as e:
        await cl.Message(content=f"❌ **API Configuration Error:** {str(e)}").send()
        return

    model = genai.GenerativeModel(
        model_name=target_model,
        generation_config={"temperature": 0.7, "top_p": 0.95}
    )
    
    memory_data = load_memory()
    learned_facts = "\n".join(memory_data["corrections"])
    
    system_context = """You are Quasar, a friendly, intelligent, and helpful AI assistant. 
    Speak in a clear, natural, and everyday conversational tone so that anyone can easily understand you. 
    Do not use space, cosmic, or overly technical metaphors. Just be polite and highly helpful.
    IMPORTANT RULES: 
    1. NEVER start your response with a large heading (like # or ##). 
    2. Always start your response with normal, regular-sized text. You may use bullet points or bold text later in the message if needed for formatting."""
    
    chat = model.start_chat(history=[])
    
    cl.user_session.set("chat", chat)
    cl.user_session.set("learned_facts", learned_facts)
    cl.user_session.set("system_context", system_context)

# --- INTERACTION LOOP ---
@cl.on_message
async def main(message: cl.Message):
    chat = cl.user_session.get("chat")
    learned_facts = cl.user_session.get("learned_facts")
    system_context = cl.user_session.get("system_context")
    
    user_text = message.content.strip()

    if "view my past chat history" in user_text.lower():
        await display_history()
        return

    learning_triggers = ["remember", "correction", "wrong", "the correct answer is"]
    if any(trigger in user_text.lower() for trigger in learning_triggers):
        memory_data = load_memory()
        memory_data["corrections"].append(f"Fact learned on {datetime.now().strftime('%Y-%m-%d')}: {user_text}")
        save_memory(memory_data)
        
        cl.user_session.set("learned_facts", "\n".join(memory_data["corrections"]))
        await cl.Message(content="✅ Got it! I have saved this to my memory and will remember it for next time.").send()
        return

    if not chat:
        return

    async with cl.Step(name="Thinking...") as step:
        step.input = user_text
        
        await asyncio.sleep(0.5)
        step.output = "Gathering information..."
        
        if learned_facts:
            context_prompt = f"System Instructions: {system_context}\n\nThings you must remember: {learned_facts}\n\nUser Message: {user_text}"
        else:
            context_prompt = f"System Instructions: {system_context}\n\nUser Message: {user_text}"
        
        res_msg = cl.Message(content="")
        
        try:
            response = await chat.send_message_async(context_prompt, stream=True)
            
            full_text = ""
            async for chunk in response:
                if chunk.text:
                    full_text += chunk.text
                    await res_msg.stream_token(chunk.text)
            
            # --- ATTACH BUTTONS ---
            history_button = cl.Action(
                name="view_history", 
                payload={"action": "fetch_history"}, 
                label="📜 View Past Logs"
            )
            
            audio_button = cl.Action(
                name="read_aloud",
                payload={"action": "read_aloud", "text": full_text}, 
                label="🔊 Read Aloud"
            )
            
            res_msg.actions = [history_button, audio_button]
            await res_msg.update()

            save_chat_log({
                "timestamp": str(datetime.now()),
                "user": user_text,
                "ai": full_text
            })

        except Exception as e:
            error_message = str(e).lower()
            
            if "429" in error_message or "quota" in error_message or "rate limit" in error_message:
                polite_reply = (
                    "⏳ **I need a quick breather!**\n\n"
                    "We have reached the free-tier limit for messages at the moment. "
                    "Please kindly wait for about **55 to 60 seconds**, and then try sending your message again. "
                    "Thank you so much for your patience! 😊"
                )
                await cl.Message(content=polite_reply).send()
            else:
                await cl.Message(content=f"⚠️ **Error:** {str(e)}").send()