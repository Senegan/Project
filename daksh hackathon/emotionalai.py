import speech_recognition as sr
import requests
import json
import pywhatkit
import datetime
import wikipedia
import pyjokes
import time
import serial  # Import serial for Bluetooth communication
from elevenlabs.client import ElevenLabs
from elevenlabs import play, save, stream, Voice, VoiceSettings

# Initialize speech recognition
recognizer = sr.Recognizer()

# Ollama API URL (Ensure Ollama is running)
OLLAMA_URL = "http://localhost:11434/api/generate"

# ElevenLabs API Setup
client = ElevenLabs(api_key="sk_7d4814e20caf8d07fb54c4810d8a4c4ee48893feb0f90474")

# Store Multiple Voices (Replace with actual Voice IDs)
voice_options = {
    "default": "xLkbQ0EhhC1XsgZVQrKl",  # Default voice
    "apj": "Z4YzxoeUzykDHgmMGrux",
}

# Set default voice
current_voice = voice_options["default"]

# Bluetooth setup (Change COM port accordingly)
bluetooth_port = "COM14"  # Example: COM5 (Windows) or /dev/ttyUSB0 (Linux/macOS)
baud_rate = 9600

try:
    # Open serial connection to HC-05
    bt_serial = serial.Serial(bluetooth_port, baud_rate, timeout=1)
    print(f"Connected to {bluetooth_port}")
except serial.SerialException as e:
    print(f"Error connecting to Bluetooth: {e}")
    bt_serial = None  # Avoid breaking the program

# Global flag to stop the loop
running = True

# Function to convert text to speech using ElevenLabs
def talk(text):
    print("AI Response:", text)  # Print the response for debugging
    audio = client.generate(text=text, voice=current_voice, stream=True)
    stream(audio)

EMERGENCY_CONTACTS = ["+917550048417"]  # Add multiple numbers if needed

# Function to send an emergency WhatsApp alert instantly
def send_emergency_alert():
    message = "⚠️ Guardian AI Alert: Motion detected, but no response from the user! Please check immediately."
    
    for contact in EMERGENCY_CONTACTS:
        print(f"Sending WhatsApp emergency alert to {contact}...")
        pywhatkit.sendwhatmsg_instantly(contact, message)
    return
# Function to send text to DeepSeek-R1 and get response
def ask_deepseek(question, max_tokens=100):
    payload = {
        "model": "deepseek-r1:1.5b",
        "prompt": question,
        "stream": False,
        "num_predict": max_tokens  # Limit output tokens
    }
    
    response = requests.post(OLLAMA_URL, json=payload)
    result = response.json()
    
    return result.get("response", "Sorry, I couldn't generate a response.")

# Function to capture voice input and process it
def listen_and_respond():
    global running, current_voice  # Allows modification of global variables

    try:
        with sr.Microphone() as source:
            print("\nListening...")
            recognizer.adjust_for_ambient_noise(source, duration=1)  # Reduce background noise
            audio = recognizer.listen(source, timeout=20)  # Listen with a timeout
            
            print("Processing speech...")
            command = recognizer.recognize_google(audio).lower()
            print("You said:", command)
            if command:
                a = 1
            # Predefined commands (Handled without AI)
            if "play" in command:
                song = command.replace("play", "").strip()
                talk("Playing " + song)
                pywhatkit.playonyt(song)

            elif "stop listening" in command or "exit" in command or "shutdown" in command:
                talk("Stopping now. Goodbye!")
                running = False  # Change flag to stop the loop
                return  # Exit function immediately

            elif "what time" in command:
                current_time = datetime.datetime.now().strftime("%I:%M %p")
                talk("It is currently " + current_time)

            elif "tell me a joke" in command:
                joke = pyjokes.get_joke()
                talk(joke)

            elif "wikipedia" in command:
                search_term = command.replace("wikipedia", "").strip()
                talk("Searching Wikipedia...")
                try:
                    result = wikipedia.summary(search_term, sentences=2)
                    talk(result)
                except wikipedia.exceptions.DisambiguationError:
                    talk("Too many results, please be more specific.")
                except wikipedia.exceptions.PageError:
                    talk("I couldn't find anything on Wikipedia.")

            # Voice Switching Command
            elif "change voice to" in command or "switch to" in command:
                new_voice = command.replace("change voice to", "").replace("switch to", "").strip()
                if new_voice in voice_options:
                    current_voice = voice_options[new_voice]
                    talk(f"Voice changed to {new_voice}")
                else:
                    talk("Voice not found. Please choose from available options.")

            # If no predefined command, use DeepSeek-R1 AI
            else:
                ai_response = ask_deepseek(command, max_tokens=500)
                talk(ai_response)

    except sr.UnknownValueError:
        a = a+1
        print(a)
        talk("Sorry, I couldn't understand.")
    except sr.RequestError:
        talk("Could not request results, check your internet connection.")
    except sr.WaitTimeoutError:
        talk("No audio detected within timeout period.")
    except Exception as e:
        print(f"An error occurred: {e}")

# Function to check for motion detection via Bluetooth

def check_motion():
    """
    Reads data from the Bluetooth module to detect motion.
    If motion is detected, the AI assistant starts automatically.
    If no response is received within 30 seconds, sends an emergency alert.
    """
    global running,a
    first_detect = 1
    a = 1
    if not bt_serial:
        return  # Skip if Bluetooth is not connected

    try:
        while running:
            data = bt_serial.readline().decode("utf-8", errors="replace").strip()  # Read Bluetooth data
            if data:
                print(f"Received from sensor: {data}")  # Debug print

                if "Object Detected! LED ON" in data:
                    if first_detect == 1:
                        talk("Hello user, I am Guardian AI. How may I help you?")
                        first_detect =+1  # Ensure greeting happens only once

                    # Start timer for response
                    

                    while a<3:  
                        try:
                            listen_and_respond()
                             # Exit loop if response detected
                        except Exception:
                            print("No response, retrying...")
                            a = a+1
                    if a==3:
                        talk("No response detected. Sending emergency alert.")
                        send_emergency_alert()
                        return
                
  # Continue normal listening

    except serial.SerialException as e:
        print(f"Bluetooth error: {e}")
    except KeyboardInterrupt:
        print("\nDisconnected from Bluetooth.")
    finally:
        if bt_serial and bt_serial.is_open:
            bt_serial.close()



# Run motion detection in the background while listening for commands
check_motion()

print("Program has stopped.")