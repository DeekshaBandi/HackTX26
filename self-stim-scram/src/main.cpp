#include <M5Unified.h>
#include <array>

// Divoom-style pixel face for M5Stack Fire (320x240).
// Background and the emotion word stay still. Only the paws bob,
// with a rare blink, so the screen does not strobe.

enum class Face {
    Happy,
    Sad,
    Angry,
    Excited,
    Sleepy,
    Love
};

struct Palette {
    uint16_t bg;
    uint16_t accent;
    uint16_t skin;
    uint16_t hair;
    uint16_t paw;
    const char* label;
};

static const std::array<Palette, 6> kPalettes = {{
    {0x0014, 0xFFE0, 0xFEB6, 0xFA60, 0xFB20, "HAPPY"},
    {0x1082, 0x7DFF, 0xFEB6, 0x9B20, 0xC560, "SAD"},
    {0x6000, 0xFD20, 0xFEB6, 0x0000, 0xF800, "ANGRY"},
    {0x000C, 0xF81F, 0xFED6, 0xFB60, 0xFE60, "EXCITED"},
    {0x1082, 0x7FE0, 0xD69A, 0x0320, 0x04A0, "SLEEPY"},
    {0x4008, 0xF81F, 0xFEB6, 0xF800, 0xF97F, "LOVE"}
}};

static constexpr int kPix = 8;
static constexpr int kOriginX = 96;
static constexpr int kOriginY = 20;
static constexpr int kSpriteCols = 16;
static constexpr int kSpriteRows = 16;
static constexpr uint32_t kBobMs = 520;
static constexpr uint32_t kBlinkEveryMs = 3200;
static constexpr uint32_t kBlinkHoldMs = 140;

static const size_t kFaceCount = kPalettes.size();
static int g_faceIndex = 0;
static int g_shownFace = -1;
static int g_bob = 0;
static bool g_eyesClosed = false;
static int g_lift = 0;
static bool g_sending = false;
static uint32_t g_lastBob = 0;
static uint32_t g_blinkStamp = 0;

static void px(int col, int row, uint16_t color) {
    if (col < 0 || row < 0 || col >= kSpriteCols || row >= kSpriteRows) return;
    M5.Display.fillRect(kOriginX + col * kPix, kOriginY + g_lift + row * kPix, kPix, kPix, color);
}

static void block(int col, int row, int w, int h, uint16_t color) {
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) px(col + x, row + y, color);
    }
}

static void drawBackdrop(const Palette& p) {
    M5.Display.fillScreen(p.bg);

    // Static pixel frame. Drawn once per emotion, never animated.
    for (int x = 0; x < 320; x += kPix) {
        M5.Display.fillRect(x, 0, kPix / 2, kPix / 2, p.accent);
        M5.Display.fillRect(x + kPix / 2, 232, kPix / 2, kPix / 2, p.accent);
    }

    // Solid plate so the word stays readable against any face color.
    M5.Display.fillRect(16, 176, 288, 52, TFT_BLACK);
    M5.Display.drawRect(16, 176, 288, 52, p.accent);
    M5.Display.setTextSize(4);
    M5.Display.setTextDatum(MC_DATUM);
    M5.Display.setTextColor(TFT_BLACK);
    M5.Display.drawString(p.label, 162, 204);
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.drawString(p.label, 160, 202);
}

static void clearSprite(const Palette& p) {
    M5.Display.fillRect(kOriginX, kOriginY - 24, kSpriteCols * kPix, kSpriteRows * kPix + 24, p.bg);
}

static void drawEyes(const Palette& p, Face face, bool closed) {
    const uint16_t white = 0xFFFF;
    if (closed || face == Face::Sleepy) {
        block(4, 6, 3, 1, TFT_BLACK);
        block(9, 6, 3, 1, TFT_BLACK);
        return;
    }
    if (face == Face::Love) {
        // Small pixel hearts.
        px(4, 5, p.accent); px(6, 5, p.accent);
        px(5, 6, p.accent);
        px(9, 5, p.accent); px(11, 5, p.accent);
        px(10, 6, p.accent);
        px(4, 6, p.accent); px(6, 6, p.accent);
        px(5, 7, p.accent);
        px(9, 6, p.accent); px(11, 6, p.accent);
        px(10, 7, p.accent);
        return;
    }
    if (face == Face::Angry) {
        // Two-pixel scowl. Eyes stay the same simple blocks as the other faces.
        px(4, 4, TFT_BLACK); px(5, 5, TFT_BLACK);
        px(11, 4, TFT_BLACK); px(10, 5, TFT_BLACK);
        block(4, 6, 3, 2, TFT_BLACK);
        block(9, 6, 3, 2, TFT_BLACK);
        px(5, 6, white);
        px(10, 6, white);
        return;
    }
    if (face == Face::Sad) {
        block(4, 6, 3, 2, TFT_BLACK);
        block(9, 6, 3, 2, TFT_BLACK);
        px(5, 6, white);
        px(10, 6, white);
        px(6, 8, p.accent);  // one still tear, not animated
        return;
    }
    // Happy / excited / default: big square eyes.
    block(4, 5, 3, 3, TFT_BLACK);
    block(9, 5, 3, 3, TFT_BLACK);
    px(5, 5, white);
    px(10, 5, white);
}

static void drawSideIcons(const Palette& p, Face face, int bob);

static void drawMouth(Face face) {
    switch (face) {
        case Face::Happy:
        case Face::Love:
            px(5, 9, TFT_BLACK);
            block(6, 10, 4, 1, TFT_BLACK);
            px(10, 9, TFT_BLACK);
            break;
        case Face::Sad:
            block(6, 9, 4, 1, TFT_BLACK);
            px(5, 10, TFT_BLACK);
            px(10, 10, TFT_BLACK);
            break;
        case Face::Angry:
            block(6, 9, 4, 1, TFT_BLACK);
            px(5, 10, TFT_BLACK);
            px(10, 10, TFT_BLACK);
            break;
        case Face::Excited:
            px(5, 8, TFT_BLACK);
            px(10, 8, TFT_BLACK);
            block(6, 9, 4, 2, TFT_BLACK);
            break;
        case Face::Sleepy:
            block(6, 10, 4, 1, TFT_BLACK);
            break;
        default:
            break;
    }
}

static void drawCharacter(const Palette& p, Face face, int bob, bool closed) {
    clearSprite(p);

    // Ears
    block(3, 1, 3, 3, p.hair);
    block(10, 1, 3, 3, p.hair);
    px(4, 2, p.skin);
    px(11, 2, p.skin);

    // Head with a 1-pixel black outline.
    block(3, 3, 10, 8, TFT_BLACK);
    block(4, 4, 8, 6, p.skin);

    if (face != Face::Angry) {
        px(4, 8, p.accent);
        px(11, 8, p.accent);
    }

    drawEyes(p, face, closed);
    drawMouth(face);

    // Body sits under the mouth so the smile stays visible.
    block(6, 12, 4, 1, p.hair);

    // Paws: two frames, one block up or down. Everything else stays put.
    const int pawRow = (bob & 1) ? 12 : 13;
    block(2, pawRow, 3, 2, TFT_BLACK);
    block(11, pawRow, 3, 2, TFT_BLACK);
    block(2, pawRow, 3, 1, p.paw);
    block(11, pawRow, 3, 1, p.paw);

    drawSideIcons(p, face, bob);
}

static void tileAt(int x, int y, uint16_t color) {
    M5.Display.fillRect(x, y + g_lift, kPix, kPix, color);
}

static void drawSteam(int x, int bob, uint16_t color) {
    const int y = bob ? 28 : 40;
    tileAt(x, y, color);
    tileAt(x + kPix, y + kPix * 2, color);
    tileAt(x, y + kPix * 4, color);
}

static void drawStar(int x, int y, uint16_t color) {
    tileAt(x + kPix, y, color);
    tileAt(x, y + kPix, color);
    tileAt(x + kPix, y + kPix, color);
    tileAt(x + kPix * 2, y + kPix, color);
    tileAt(x + kPix, y + kPix * 2, color);
}

static void drawSun(int x, int y, uint16_t color) {
    tileAt(x + kPix, y, color);
    tileAt(x + kPix * 2, y, color);
    tileAt(x, y + kPix, color);
    tileAt(x + kPix, y + kPix, color);
    tileAt(x + kPix * 2, y + kPix, color);
    tileAt(x + kPix * 3, y + kPix, color);
    tileAt(x, y + kPix * 2, color);
    tileAt(x + kPix, y + kPix * 2, color);
    tileAt(x + kPix * 2, y + kPix * 2, color);
    tileAt(x + kPix * 3, y + kPix * 2, color);
    tileAt(x + kPix, y + kPix * 3, color);
    tileAt(x + kPix * 2, y + kPix * 3, color);
}

static void drawRain(int x, int y, uint16_t color) {
    tileAt(x + kPix, y, color);
    tileAt(x, y + kPix, color);
    tileAt(x + kPix, y + kPix, color);
    tileAt(x + kPix * 2, y + kPix, color);
    tileAt(x + kPix, y + kPix * 3, color);
    tileAt(x + kPix, y + kPix * 4, color);
}

static void drawZ(int x, int y, uint16_t color) {
    // Top bar, three-step diagonal, bottom bar.
    tileAt(x, y, color);
    tileAt(x + kPix, y, color);
    tileAt(x + kPix * 2, y, color);
    tileAt(x + kPix * 2, y + kPix, color);
    tileAt(x + kPix, y + kPix * 2, color);
    tileAt(x, y + kPix * 3, color);
    tileAt(x, y + kPix * 4, color);
    tileAt(x + kPix, y + kPix * 4, color);
    tileAt(x + kPix * 2, y + kPix * 4, color);
}

static void drawHeart(int x, int y, uint16_t color) {
    tileAt(x, y, color);
    tileAt(x + kPix * 2, y, color);
    tileAt(x, y + kPix, color);
    tileAt(x + kPix, y + kPix, color);
    tileAt(x + kPix * 2, y + kPix, color);
    tileAt(x + kPix, y + kPix * 2, color);
}

static void drawSideIcons(const Palette& p, Face face, int bob) {
    M5.Display.fillRect(8, 8, 80, 150, p.bg);
    M5.Display.fillRect(232, 8, 80, 150, p.bg);
    switch (face) {
        case Face::Happy:
            drawSun(12, 44, p.accent);
            drawSun(264, 44, p.accent);
            break;
        case Face::Sad:
            drawRain(20, 36, p.accent);
            drawRain(272, 36, p.accent);
            break;
        case Face::Angry:
            drawSteam(16, bob, p.accent);
            drawSteam(280, bob, p.accent);
            break;
        case Face::Excited:
            drawStar(20, 48, p.accent);
            drawStar(268, 48, p.accent);
            break;
        case Face::Sleepy:
            drawZ(20, 44, p.accent);
            drawZ(272, 44, p.accent);
            break;
        case Face::Love:
            drawHeart(20, 48, p.accent);
            drawHeart(276, 48, p.accent);
            break;
        default:
            break;
    }
}

static void showFace(bool redrawBackdrop) {
    const Palette& p = kPalettes[g_faceIndex];
    if (redrawBackdrop) drawBackdrop(p);
    drawCharacter(p, static_cast<Face>(g_faceIndex), g_bob, g_eyesClosed);
    g_shownFace = g_faceIndex;
}

static void switchFace(int direction) {
    if (direction < 0) {
        g_faceIndex = (g_faceIndex == 0) ? static_cast<int>(kFaceCount - 1) : g_faceIndex - 1;
    } else {
        g_faceIndex = (g_faceIndex + 1) % static_cast<int>(kFaceCount);
    }
    g_bob = 0;
    g_eyesClosed = false;
}

static const char* currentFaceName() {
    return kPalettes[g_faceIndex].label;
}

static void playSendHop() {
    // One slow hop of the face and side icons. The background and word stay put.
    static const int kHops[] = {0, -8, -16, -8, 0};
    for (int hop : kHops) {
        g_lift = hop;
        showFace(false);
        delay(140);
    }
    g_lift = 0;
}

static void sendCurrentEmoji() {
    // The board is on the same computer as the app, over the USB cable.
    Serial.print("#EMOJI ");
    Serial.println(currentFaceName());
    Serial.flush();
}

void setup() {
    auto cfg = M5.config();
    cfg.internal_imu = false;
    cfg.internal_mic = false;
    M5.begin(cfg);
    Serial.begin(115200);

    M5.Display.setBrightness(200);
    M5.Display.setRotation(1);
    M5.Display.setTextSize(4);
    showFace(true);
    g_lastBob = millis();
    g_blinkStamp = g_lastBob;

    // The center button pin reads as pressed once at boot. Clear that
    // before the loop, or the face is sent without a real press.
    const uint32_t settleUntil = millis() + 600;
    while ((int32_t)(settleUntil - millis()) > 0) {
        M5.update();
        (void)M5.BtnA.wasPressed();
        (void)M5.BtnB.wasPressed();
        (void)M5.BtnC.wasPressed();
        delay(10);
    }
}

void loop() {
    M5.update();

    if (!g_sending && M5.BtnB.wasPressed()) {
        g_sending = true;
        playSendHop();
        sendCurrentEmoji();
        g_sending = false;
        g_lastBob = millis();
        g_blinkStamp = g_lastBob;
    }
    if (M5.BtnA.wasPressed()) switchFace(-1);
    if (M5.BtnC.wasPressed()) switchFace(1);

    if (g_sending) return;

    const uint32_t now = millis();
    bool dirty = (g_shownFace != g_faceIndex);

    if (now - g_lastBob >= kBobMs) {
        g_lastBob = now;
        g_bob ^= 1;
        dirty = true;
    }

    if (!g_eyesClosed && (now - g_blinkStamp) >= kBlinkEveryMs) {
        g_eyesClosed = true;
        g_blinkStamp = now;
        dirty = true;
    } else if (g_eyesClosed && (now - g_blinkStamp) >= kBlinkHoldMs) {
        g_eyesClosed = false;
        g_blinkStamp = now;
        dirty = true;
    }

    if (dirty) showFace(g_shownFace != g_faceIndex);

    delay(20);
}
