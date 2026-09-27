// Pixels: PNG in/out (StbImageSharp / StbImageWriteSharp, public domain), mip chains, BCn via BCnEncoder.Net (MIT).
using BCnEncoder.Decoder;
using BCnEncoder.Encoder;
using BCnEncoder.Shared;
using StbImageSharp;
using StbImageWriteSharp;

namespace B4BMod;

static class Codec
{
    public static (byte[] rgba, int w, int h) ReadPng(string path)
    {
        using var f = File.OpenRead(path);
        var img = ImageResult.FromStream(f, StbImageSharp.ColorComponents.RedGreenBlueAlpha);
        return (img.Data, img.Width, img.Height);
    }

    public static void WritePng(string path, byte[] rgba, int w, int h)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        using var f = File.Create(path);
        new ImageWriter().WritePng(rgba, w, h, StbImageWriteSharp.ColorComponents.RedGreenBlueAlpha, f);
    }

    static readonly float[] SrgbToLin = Enumerable.Range(0, 256).Select(i =>
    {
        float c = i / 255f;
        return c <= 0.04045f ? c / 12.92f : MathF.Pow((c + 0.055f) / 1.055f, 2.4f);
    }).ToArray();

    static byte LinToSrgb(float l)
    {
        l = Math.Clamp(l, 0, 1);
        float c = l <= 0.0031308f ? l * 12.92f : 1.055f * MathF.Pow(l, 1 / 2.4f) - 0.055f;
        return (byte)Math.Clamp((int)MathF.Round(c * 255), 0, 255);
    }

    // Box-filter halving (colour channels in linear light for sRGB textures, like the engine's mip generator).
    // Normal maps (BC5): X/Y averaged and renormalised.
    public static List<(byte[] px, int w, int h)> MipChain(byte[] rgba, int w, int h, int levels, bool srgb, bool normal)
    {
        var chain = new List<(byte[], int, int)> { (rgba, w, h) };
        for (int l = 1; l < levels; l++)
        {
            int nw = Math.Max(1, w / 2), nh = Math.Max(1, h / 2);
            var o = new byte[nw * nh * 4];
            var acc = new float[4];
            for (int y = 0; y < nh; y++)
            for (int x = 0; x < nw; x++)
            {
                Array.Clear(acc);
                int n = 0;
                for (int dy = 0; dy < (h > 1 ? 2 : 1); dy++)
                for (int dx = 0; dx < (w > 1 ? 2 : 1); dx++)
                {
                    int i = ((y * 2 + dy) * w + (x * 2 + dx)) * 4;
                    for (int c = 0; c < 4; c++)
                        acc[c] += srgb && c < 3 ? SrgbToLin[rgba[i + c]] : rgba[i + c] / 255f;
                    n++;
                }
                int j = (y * nw + x) * 4;
                for (int c = 0; c < 4; c++) acc[c] /= n;
                if (normal)
                {
                    float nx = acc[0] * 2 - 1, ny = acc[1] * 2 - 1, nz = acc[2] * 2 - 1;
                    float len = MathF.Sqrt(nx * nx + ny * ny + nz * nz);
                    if (len > 1e-5f) { acc[0] = (nx / len + 1) / 2; acc[1] = (ny / len + 1) / 2; acc[2] = (nz / len + 1) / 2; }
                }
                for (int c = 0; c < 4; c++)
                    o[j + c] = srgb && c < 3 ? LinToSrgb(acc[c]) : (byte)Math.Clamp((int)MathF.Round(acc[c] * 255), 0, 255);
            }
            rgba = o; w = nw; h = nh;
            chain.Add((rgba, w, h));
        }
        return chain;
    }

    // Bilinear resample (for --resize); sRGB-aware.
    public static byte[] Resize(byte[] src, int sw, int sh, int dw, int dh, bool srgb)
    {
        // downscale by repeated halving while possible (good quality), then bilinear for the rest
        while (sw >= dw * 2 && sh >= dh * 2 && sw % 2 == 0 && sh % 2 == 0)
        {
            var c = MipChain(src, sw, sh, 2, srgb, false)[1];
            src = c.px; sw = c.w; sh = c.h;
        }
        if (sw == dw && sh == dh) return src;
        var o = new byte[dw * dh * 4];
        for (int y = 0; y < dh; y++)
        for (int x = 0; x < dw; x++)
        {
            float fx = (x + 0.5f) * sw / dw - 0.5f, fy = (y + 0.5f) * sh / dh - 0.5f;
            int x0 = Math.Clamp((int)MathF.Floor(fx), 0, sw - 1), y0 = Math.Clamp((int)MathF.Floor(fy), 0, sh - 1);
            int x1 = Math.Min(x0 + 1, sw - 1), y1 = Math.Min(y0 + 1, sh - 1);
            float tx = Math.Clamp(fx - x0, 0, 1), ty = Math.Clamp(fy - y0, 0, 1);
            for (int c = 0; c < 4; c++)
            {
                float V(int xx, int yy) { byte b = src[(yy * sw + xx) * 4 + c]; return srgb && c < 3 ? SrgbToLin[b] : b / 255f; }
                float v = (V(x0, y0) * (1 - tx) + V(x1, y0) * tx) * (1 - ty) + (V(x0, y1) * (1 - tx) + V(x1, y1) * tx) * ty;
                o[(y * dw + x) * 4 + c] = srgb && c < 3 ? LinToSrgb(v) : (byte)Math.Clamp((int)MathF.Round(v * 255), 0, 255);
            }
        }
        return o;
    }

    static CompressionFormat Bcn(string pf) => pf switch
    {
        "PF_DXT1" => CompressionFormat.Bc1,
        "PF_DXT3" => CompressionFormat.Bc2,
        "PF_DXT5" => CompressionFormat.Bc3,
        "PF_BC4" => CompressionFormat.Bc4,
        "PF_BC5" => CompressionFormat.Bc5,
        "PF_BC7" => CompressionFormat.Bc7,
        _ => CompressionFormat.Unknown,
    };

    public static byte[] Encode(string pf, byte[] rgba, int w, int h, string quality)
    {
        switch (pf)
        {
            case "PF_B8G8R8A8":
                var o = new byte[w * h * 4];
                for (int i = 0; i < w * h; i++) { o[i * 4] = rgba[i * 4 + 2]; o[i * 4 + 1] = rgba[i * 4 + 1]; o[i * 4 + 2] = rgba[i * 4]; o[i * 4 + 3] = rgba[i * 4 + 3]; }
                return o;
            case "PF_G8":
                return Enumerable.Range(0, w * h).Select(i => rgba[i * 4]).ToArray();
        }
        var fmt = Bcn(pf);
        if (fmt == CompressionFormat.Unknown) throw new Exception($"pixel format {pf} not supported");
        // BCn works on 4x4 blocks: pad the 2x2 and 1x1 mips to 4x4 by repeating edge pixels
        int pw = Math.Max(4, w), ph = Math.Max(4, h);
        if (pw != w || ph != h)
        {
            var p = new byte[pw * ph * 4];
            for (int y = 0; y < ph; y++) for (int x = 0; x < pw; x++)
                Array.Copy(rgba, ((Math.Min(y, h - 1)) * w + Math.Min(x, w - 1)) * 4, p, (y * pw + x) * 4, 4);
            rgba = p;
        }
        var enc = new BcEncoder(fmt);
        enc.OutputOptions.GenerateMipMaps = false;
        enc.OutputOptions.Quality = quality switch { "fast" => CompressionQuality.Fast, "best" => CompressionQuality.BestQuality, _ => CompressionQuality.Balanced };
        enc.Options.IsParallel = true;
        return enc.EncodeToRawBytes(rgba, pw, ph, PixelFormat.Rgba32, 0, out _, out _);
    }

    public static byte[] Decode(string pf, byte[] data, int w, int h)
    {
        switch (pf)
        {
            case "PF_B8G8R8A8":
                var o = new byte[w * h * 4];
                for (int i = 0; i < w * h; i++) { o[i * 4] = data[i * 4 + 2]; o[i * 4 + 1] = data[i * 4 + 1]; o[i * 4 + 2] = data[i * 4]; o[i * 4 + 3] = data[i * 4 + 3]; }
                return o;
            case "PF_G8":
                return data.SelectMany(g => new[] { g, g, g, (byte)255 }).ToArray();
        }
        var fmt = Bcn(pf);
        if (fmt == CompressionFormat.Unknown) throw new Exception($"pixel format {pf} not supported");
        int pw = Math.Max(4, w), ph = Math.Max(4, h);
        var px = new BcDecoder().DecodeRaw(data, pw, ph, fmt);
        var rgba = new byte[w * h * 4];
        for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
        {
            var c = px[y * pw + x]; int i = (y * w + x) * 4;
            rgba[i] = c.r; rgba[i + 1] = c.g; rgba[i + 2] = c.b; rgba[i + 3] = c.a;
            if (fmt == CompressionFormat.Bc5)
            {
                float nx = c.r / 127.5f - 1, ny = c.g / 127.5f - 1;
                rgba[i + 2] = (byte)Math.Clamp((int)MathF.Round((MathF.Sqrt(MathF.Max(0, 1 - nx * nx - ny * ny)) + 1) * 127.5f), 0, 255);
                rgba[i + 3] = 255;
            }
            if (fmt is CompressionFormat.Bc1 or CompressionFormat.Bc4) rgba[i + 3] = 255;
        }
        return rgba;
    }
}
