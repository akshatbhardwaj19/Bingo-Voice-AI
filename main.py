import speech_recognition as sr
import pyttsx3
import webbrowser
import musicLibrary
import time
import difflib
import json
import os
from dotenv import load_dotenv

from vosk import Model, KaldiRecognizer
import sounddevice as sd
import queue
import socket
import threading
import requests
import pvporcupine
import pyaudio
import struct

load_dotenv()
PORCUPINE_API_KEY = os.getenv("PORCUPINE_API_KEY")

# vosk (offline speech recognition)
MODEL_PATH = "models/vosk-model-small-en-us-0.15"
vosk_model = Model(MODEL_PATH)
audio_queue = queue.Queue()

last_trigger_time = 0
WAKE_COOLDOWN = 3  # seconds

r = sr.Recognizer()
r.energy_threshold = 300
r.dynamic_energy_threshold = False
r.pause_threshold = 0.8

MEMORY_FILE = "memory.json"
conversation_history = ""

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=4)

def speak(text):
    engine = pyttsx3.init()
    print(f"Speaking: {text}")
    engine.say(text)
    engine.runAndWait()
    engine.stop()
    print("Done speaking")

# helper function for offline speech recognition
def audio_callback(indata, frames, time, status):
    if status:
        print(status)
    audio_queue.put(bytes(indata))   

def is_internet_available():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except OSError:
        return False

def listen_command(timeout=5, phrase_time_limit=4):
    with sr.Microphone() as source:
        print("Listening...")
        audio = r.listen(
            source,
            timeout=timeout,
            phrase_time_limit=phrase_time_limit
        )

    try:
        text = r.recognize_google(audio)
        print("Recognized:", text)
        return text.lower()
    except sr.UnknownValueError:
        print("Could not understand audio")
        return ""
    except sr.WaitTimeoutError:
        print("Listening timed out")
        return ""
    
def listen_command_offline(timeout=6):
    recognizer = KaldiRecognizer(vosk_model, 16000)
    recognizer.SetWords(False)

    with sd.RawInputStream(
        samplerate=16000,
        blocksize=8000,
        dtype="int16",
        channels=1,
        callback=audio_callback
    ):
        print("Listening (offline)...")
        start_time = time.time()

        while True:
            if time.time() - start_time > timeout:
                break

            data = audio_queue.get()
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "")
                if text:
                    print("Recognized (offline):", text)
                    return text

    return ""  

def listen_smart(timeout=6, phrase_time_limit=6):
    if is_internet_available():
        print("Using ONLINE recognition...")
        try:
            return listen_command(timeout, phrase_time_limit)
        
        except sr.RequestError:
            # Only switch if API/network error
            print("Network/API error. Switching to offline.")
            return listen_command_offline(timeout)
        
        except sr.WaitTimeoutError:
            print("User did not speak in time.")
            return ""
        
        except sr.UnknownValueError:
            print("Speech unclear.")
            return ""
    else:
        print("No internet. Using OFFLINE recognition...")
        return listen_command_offline(timeout)
    
def fuzzy_match(command, choices, cutoff=0.6):
    
    # Returns the best matching choice if similarity >= cutoff
    matches = difflib.get_close_matches(
        command,
        choices,
        n=1,
        cutoff=cutoff
    )
    return matches[0] if matches else None

def ask_llm(prompt, memory_context=""):
    try:
        full_prompt = f"""You are Bingo, a helpful voice assistant.
                          Be concise and conversational.
                          Previous context:{memory_context}
                          User: {prompt}
                          Assistant:"""

        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3:8b",
                "prompt": full_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 200
                }
            }
        )

        data = response.json()
        return data["response"].strip()

    except Exception as e:
        print("LLM error: ", e)
        return "AI brain is currently offline."

def processCommand(c):
    global memory
    c = c.lower()
    
    if "remember that" in c:
        speak("Okay, tell me the full sentence")

        try:
            # fact = listen_command(
            #     timeout=6,
            #     phrase_time_limit=8   # 🔥 longer capture
            # ).lower()
            # fact = listen_command_offline(timeout=8).lower()
            fact = listen_smart(timeout=8, phrase_time_limit=8)

            if "my name is" in fact:
                name = fact.replace("my name is", "").strip()
                memory["name"] = name
                speak(f"Got it. I will remember your name is {name}")
            else:
                memory["note"] = fact
                speak("I have remembered that")
            save_memory(memory)
        except:
            speak("I couldn't hear the full sentence")
        return
    
    elif ("what is my name" in c) or ("what's my name" in c):
        if "name" in memory:
            speak(f"Your name is {memory['name']}")
        else:
            speak("I don't know your name yet")
        return
    


    COMMANDS = {
        "open google": "https://www.google.com/",
        "open youtube": "https://www.youtube.com/",
        "open linkedin": "https://in.linkedin.com/",
        "open instagram": "https://www.instagram.com/",
    }

    # Fuzzy match site commands
    best_match = fuzzy_match(c, COMMANDS.keys(), cutoff=0.55)

    if best_match:
        speak(f"Opening {best_match.replace('open ', '')}")
        webbrowser.open(COMMANDS[best_match])
        return

    # Fuzzy match play command
    if c.startswith("play"):
        song = c.split(" ", 1)[1]

        best_song = fuzzy_match(
            song,
            musicLibrary.music.keys(),
            cutoff=0.5
        )

        if best_song:
            speak(f"Playing {best_song}")
            webbrowser.open(musicLibrary.music[best_song])
        else:
            speak("I couldn't find that song")
        return

    # speak("Sorry, I didn't understand the command")

    global conversation_history
    reply = ask_llm(c, conversation_history)

    conversation_history += f"\nUser: {c}\nAssistant: {reply}"
    # limit memory size
    conversation_history = conversation_history[-2000:]
    speak(reply)

# def is_wake_word(text):
#     if not text:
#         return False

#     text = text.lower().strip()
#     wake_variants = ["bingo"]

#     # check each word individually
#     words = text.split()

#     for word in words:
#         match = difflib.get_close_matches(word, wake_variants, n=1, cutoff=0.75)
#         if match:
#             return True

#     return False

# def wake_word_listener():
#     global last_trigger_time

#     while True:
#         try:
#             word = listen_smart(timeout=2, phrase_time_limit=2)

#             # if word and "bingo" in word.lower():
#             if is_wake_word(word):
#                 if time.time() - last_trigger_time > WAKE_COOLDOWN:
#                     last_trigger_time = time.time()

#                     print("Bingo detected")
#                     speak("Bingo active")

#                     command = listen_smart(timeout=5, phrase_time_limit=4)

#                     if command:
#                         print("Command received:", command)
#                         processCommand(command)

#         except Exception as e:
#             print("Wake listener error:", e)

def wake_word_listener():
    global last_trigger_time

    porcupine = pvporcupine.create(
        access_key=PORCUPINE_API_KEY,
        keyword_paths=["bingo_en_windows_v4_0_0.ppn"]  
    )

    pa = pyaudio.PyAudio()

    stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length
    )

    print("Listening for wake word...")

    while True:
        pcm = stream.read(porcupine.frame_length)
        pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)

        result = porcupine.process(pcm)

        if result >= 0:
            if time.time() - last_trigger_time > WAKE_COOLDOWN:
                last_trigger_time = time.time()

                print("Bingo Detected!")
                speak("Bingo active")

                command = listen_smart(timeout=5, phrase_time_limit=5)
                if command:
                    processCommand(command)

if __name__ == "__main__":
    with sr.Microphone() as source:
        print("Calibrating microphone...")
        r.adjust_for_ambient_noise(source, duration=2)
        print("Calibration complete")
        r.energy_threshold = r.energy_threshold * 1.2

    memory = load_memory()    
    speak("Hey! I am Bingo. Say Bingo to wake me up.")

    listener_thread = threading.Thread(
        target=wake_word_listener,
        daemon=True
    )

    listener_thread.start()

    # Keep main thread alive
    while True:
        time.sleep(1)

    # while True:
    #     try:
    #         # word = listen_command()
    #         # word = listen_command_offline(timeout=3)
    #         word = listen_smart(timeout=3, phrase_time_limit=3)

    #         if "bingo" in word.lower():
    #             if time.time() - last_trigger_time > WAKE_COOLDOWN:
    #                 last_trigger_time = time.time()

    #                 print("Detected 'bingo'")
    #                 speak("bingo active")

    #                 # command = listen_command(timeout=4, phrase_time_limit=4)
    #                 # command = listen_command_offline(timeout=5)
    #                 command = listen_smart(timeout=4, phrase_time_limit=4)

    #                 print("Command received: ", command)
    #                 processCommand(command)

    #     except Exception as e:
    #         print("Error:", e)

