import keyboard

def main():
    print("Warte auf Eingaben vom Selfie-Stick...")
    print("Drücke den Button am Controller (STRG+C zum Beenden)\n")

    try:
        while True:
            event = keyboard.read_event()

            if event.event_type == keyboard.KEY_DOWN:
                print(f"Taste gedrückt: {event.name}")

            elif event.event_type == keyboard.KEY_UP:
                print(f"Taste losgelassen: {event.name}")

    except KeyboardInterrupt:
        print("\nBeendet.")

if __name__ == "__main__":
    main()
