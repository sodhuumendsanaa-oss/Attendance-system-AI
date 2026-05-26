# Automatic AI attendance system for late students

This is an advanced, automated AI-based attendance management system designed to handle late students efficiently. It uses facial recognition to track attendance and integrates directly with Slack and public transport delay certificates to automatically excuse tardiness.

## ✨ Features

- **Facial Recognition (OpenCV LBPH)**: Fast, lightweight face detection and recognition using local machine learning.
- **Smart Period Management**: Configurable class periods with distinct "Present" and "Late" time windows.
- **Automated Slack Integration**: Students can send a direct message to a Slack bot (e.g., `StudentName: Delayed 30 mins by Chuo Line`), and the system will automatically extend their "Late" threshold and mark them as "Present" when they arrive.
- **Real-time Train Delays (JR East)**: Scrapes and displays current public transportation delays natively on the dashboard for manual or automated verification.
- **Modern Dashboard**: A clean, responsive UI to manage known faces, attendance logs, and system settings.

## 🚀 Technologies
- **Backend**: Python, Flask, SQLite3
- **Computer Vision**: OpenCV (Haar Cascades, LBPHFaceRecognizer)
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Integration**: Slack Bolt API (Socket Mode)

## ⚠️ Security Notice
This repository does not include the local database (`face_data.db`), captured images (`captures/`), or the `.bat` files containing private Slack tokens.
