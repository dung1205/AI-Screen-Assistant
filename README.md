# AI Screen

Desktop AI Assistant built with Python.

ai_screen/
│
├── main.py
├── requirements.txt
├── README.md
├── .gitignore
│
├── app/
│   │
│   ├── AppController.py
│   │
│   ├── views/
│   │   ├── HeaderView.py
│   │   ├── SideBarView.py
│   │   ├── ChatArea.py
│   │   ├── InputBarView.py
│   │   └── MessageBubble.py
│   │
│   ├── logic/
│   │   └── ChatLogic.py
│   │
│   ├── services/
│   │   ├── GeminiClient.py
│   │   ├── ScreenshotService.py
│   │   ├── ImageService.py
│   │   ├── ChatDB.py
│   │   └── KeyStorage.py
│   │
│   └── data/
│       └── .gitkeep
│
├── tests/
│   ├── test_screenshot.py
│   ├── test_image.py
│   ├── test_gemini.py
│   ├── test_chat_db.py
│   └── test_chat_logic.py
│
└── assets/
    ├── icons/
    └── images/
```
