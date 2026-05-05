PADEL Pro App starten

Lokal:
1) Ordner öffnen
2) Terminal/cmd im Ordner starten
3) Installieren:
   pip install -r requirements.txt
4) Starten:
   python app.py
5) Browser öffnen:
   http://127.0.0.1:5000

Render/GitHub:
- Repository mit diesen Dateien erstellen
- Auf Render als Web Service deployen
- Build Command: pip install -r requirements.txt
- Start Command: gunicorn --worker-class eventlet -w 1 app:app
- Für dauerhafte Speicherung PostgreSQL anlegen und DATABASE_URL setzen

Wichtige Änderungen in dieser Version:
- Dark Mode wurde komplett entfernt.
- Jeder Court hat einen eigenen Timer.
- Start/Weiter/Pause im Controller betrifft nur den jeweiligen Court.
- Zeitansagen kommen nur im Controller und nur für die eigene Court-Zeit.
- Punkte werden serverseitig blockiert, wenn der Court-Timer abgelaufen oder nicht gestartet ist.
- Finals bleiben ohne Zeitlimit und werden manuell beendet.

git remote add origin https://github.com/ptph94cnrn-dot/PADEL-Pro.git
SECRET_KEY=asdhjkl123!§$%ASDklj234