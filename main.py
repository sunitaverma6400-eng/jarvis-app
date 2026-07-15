"""
Jarvis - Main Entry Point (Terminal mode)
-------------------------------------------
Chalane ka tarika:
    python main.py            -> text chat mode
    python main.py --voice    -> voice mode (bolke baat karo)

Naya API key sikhane ke liye, kabhi bhi type/bolo:
    Jarvis code api: groq gsk_xxxxxxxx

(Baaki sab — weather, alarm, news, API keys delete karna, waghera — ab Jarvis
khud function-calling se samajh leta hai, koi special command yaad rakhne ki
zarurat nahi.)

Note: Yeh ab purana/optional mode hai. Roz ke use ke liye server.py +
browser wala web-app version zyada behtar hai.
"""

import sys
import memory
import brain
import tools
import voice

HISTORY_LIMIT = 16


def handle_groq_key(user_text: str):
    """
    Sirf Groq ki apni key text-pattern se save hoti hai (chicken-and-egg:
    function calling khud Groq key maangta hai). Baaki sab Jarvis khud
    function-calling se samajhta hai.
    """
    api_cmd = memory.try_extract_api_command(user_text)
    if api_cmd and api_cmd[0] == "groq":
        memory.save_secret("groq", api_cmd[1])
        return True, "Theek hai, maine Groq API key yaad rakh li hai."
    return False, None


def chat_loop(use_voice: bool = False):
    history = []
    print("🤖 Jarvis taiyaar hai. Baat shuru karo (exit/bye bolke band karo)\n")

    # Background thread jo stream disconnect/finish hone par TTS se batayega
    # (sirf tab active hota hai jab isi machine par ffplay/vlc mile — Termux)
    tools.start_stream_monitor()

    if use_voice:
        voice.speak("Jarvis taiyaar hai. Main sun raha hoon.")

    while True:
        if use_voice:
            print("[Sun raha hoon...]")
            user_text, err = voice.listen_from_mic(duration=5)
            if err:
                print(f"⚠️ {err}")
                continue
            if not user_text:
                continue
            print(f"Tum: {user_text}")
        else:
            try:
                user_text = input("Tum: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nJarvis band ho raha hai. Alvida!")
                break

        if not user_text:
            continue

        if user_text.lower() in ("exit", "bye", "band ho jao", "quit"):
            print("Jarvis: Theek hai, alvida! 👋")
            if use_voice:
                voice.speak("Theek hai, alvida!")
            break

        handled, response = handle_groq_key(user_text)

        if not handled:
            handled, response = brain.handle_stream_command(user_text)

        if not handled:
            response = brain.ask_jarvis(history, user_text)
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": response})
            history = history[-HISTORY_LIMIT:]

        print(f"Jarvis: {response}\n")
        if use_voice:
            voice.speak(response)


if __name__ == "__main__":
    voice_mode = "--voice" in sys.argv
    chat_loop(use_voice=voice_mode)
