/// The Metal shaders, compiled when the game starts (SwiftPM has no build step for
/// .metal files).
///
/// Everything is placed in the original's 320x200 screen space; the viewport
/// stretches that over the window. Land is given in view corners and height steps
/// and projected with the original's own arithmetic (see `CloseUp.screenPoint`).
/// The ground textures are layers of one texture array, in the order of
/// `GroundTextures.names`.
let shaderSource = """
#include <metal_stdlib>
using namespace metal;

struct Uniforms {
    float4 light;   // xyz: direction towards the sun, in cells (z up)
    float4 fill;    // xyz: a softer second light
    float4 params;  // x: seconds; y: how snowy models are; zw: map cell at the view's top corner
    float4 layers;  // x: the landscape's ground; y: its beaches; z: 1 for meadows; w: 1 if its sea is lava
};

// Layers of the ground texture array.
constant uint FARMLAND = 6, SCORCHED = 7, EARTH = 8, WATER = 9, LAVA = 10, NOISE = 11, ROCK = 12, MEADOW = 13;
// A height step in cell widths.
constant float STEP = 0.40824829;

static float4 clipPosition(float2 screen, float depth) {
    return float4(screen.x / 160.0 - 1.0, 1.0 - screen.y / 100.0, depth, 1.0);
}

// The original's projection of a point in view corners (x, y) and height steps
// (z), and its depth: nearer the viewer is larger x + y and higher.
static float4 project(float3 p) {
    float2 screen = float2(192.0 + 16.0 * (p.x - p.y), 72.0 + 8.0 * (p.x + p.y) - 8.0 * p.z);
    return clipPosition(screen, 0.95 - (p.x + p.y + 2.0 * p.z) / 56.0);
}

// Towards the viewer, with heights in cell widths.
constant float3 VIEW = float3(0.6123724, 0.6123724, 0.5);

// Warm sunshine from the left, a blue sky above and a little cool light bounced
// from the right.
static float3 shade(float3 albedo, float3 n, constant Uniforms &u) {
    float sun = max(dot(n, u.light.xyz), 0.0);
    float sky = 0.62 + 0.38 * n.z;
    float fill = max(dot(n, u.fill.xyz), 0.0);
    float3 light = float3(1.0, 0.93, 0.78) * (0.9 * sun) + float3(0.50, 0.58, 0.72) * (0.55 * sky)
        + float3(0.40, 0.46, 0.58) * (0.25 * fill);
    return albedo * light;
}

// Pictures: the board, dialogs, the original's tiles and sprites.

struct QuadVertex { float2 position; float2 uv; };
struct QuadOut { float4 position [[position]]; float2 uv; };

// `depth` places a picture among the 3D land and models; whole-screen layers
// pass 0 and are drawn without depth testing.
vertex QuadOut quad_vertex(
    uint id [[vertex_id]], const device QuadVertex *vertices [[buffer(0)]], constant float &depth [[buffer(1)]]
) {
    QuadOut out;
    out.position = clipPosition(vertices[id].position, depth);
    out.uv = vertices[id].uv;
    return out;
}

fragment float4 quad_fragment(QuadOut in [[stage_in]], texture2d<float> picture [[texture(0)]]) {
    constexpr sampler pixels(filter::nearest, address::clamp_to_edge);
    float4 color = picture.sample(pixels, in.uv);
    if (color.a < 0.5) discard_fragment();
    return color;
}

// Land, sea and the earth under the view's edges.

struct LandVertex {
    packed_float3 position;  // view corners x, y; height steps z
    packed_float3 normal;
    packed_float4 weights;   // see CloseUpMesh.LandVertex
    float surface;           // 0 land, 1 sea, 2 earth wall
};

struct LandOut {
    float4 position [[position]];
    float3 place;
    float3 normal;
    float4 weights;
    float surface [[flat]];
};

vertex LandOut land_vertex(uint id [[vertex_id]], const device LandVertex *vertices [[buffer(0)]]) {
    LandVertex v = vertices[id];
    LandOut out;
    out.place = float3(v.position);
    out.position = project(out.place);
    out.normal = float3(v.normal);
    out.weights = float4(v.weights);
    out.surface = v.surface;
    return out;
}

constexpr sampler ground(filter::linear, mip_filter::linear, address::repeat, max_anisotropy(8));

// A texture at two scales mixed, so its repeats are hard to see. The textures
// are painted lit from their top left; turned like this, that is the sun's side.
static float3 groundColor(texture2d_array<float> textures, float2 uv, uint layer) {
    float2 turned = float2(-uv.y, uv.x);
    float3 near = textures.sample(ground, turned, layer).rgb;
    float3 far = textures.sample(ground, turned * 0.43 + float2(0.31, 0.67), layer).rgb;
    return mix(near, far, 0.35);
}

// Sharpens a blend weight into a ragged edge; 0 and 1 stay as they are.
static float ragged(float weight, float noise) {
    return smoothstep(0.42, 0.58, weight + (noise - 0.5) * 0.3);
}

fragment float4 land_fragment(
    LandOut in [[stage_in]], constant Uniforms &u [[buffer(1)]], texture2d_array<float> textures [[texture(0)]]
) {
    float2 map = in.place.xy + u.params.zw;  // map cells, fixed to the land
    float2 uv = map * 0.5;                   // each texture covers 2x2 cells
    float t = u.params.x;
    uint landLayer = uint(u.layers.x), beachLayer = uint(u.layers.y);
    float macro = textures.sample(ground, map * 0.07, NOISE).r;
    float patches = textures.sample(ground, map * 0.16 + float2(0.37, 0.71), NOISE).r;
    float detail = textures.sample(ground, map * 0.9, NOISE).r;
    float fine = textures.sample(ground, map * 2.3 + float2(0.5, 0.2), NOISE).r;
    float3 n = normalize(in.normal);

    if (in.surface > 1.5) {
        // Earth in bands fixed to height, with a lip of turf along the top.
        float2 cut = float2((map.x + map.y) * 0.5, -in.place.z * STEP * 0.5);
        float3 color = textures.sample(ground, cut, EARTH).rgb;
        float below = in.weights.x;
        color *= mix(0.72, 1.0, smoothstep(0.1, 0.7, below));
        color = mix(groundColor(textures, uv, landLayer), color, smoothstep(0.07, 0.14, below));
        return float4(min(shade(color, n, u), float3(1.0)), 1.0);
    }

    // The coast: the sea's cells blurred, with its line broken up.
    float coast = in.weights.x - 0.5 + (detail - 0.5) * 0.16 + (fine - 0.5) * 0.06;

    if (coast > 0.0) {
        float out = in.weights.w;  // cells from land
        if (u.layers.w > 0.5) {
            // Lava glows by itself and churns slowly, crusting over by the shore.
            float3 a = textures.sample(ground, uv * 0.8 + float2(t * 0.010, t * 0.006), LAVA).rgb;
            float3 b = textures.sample(ground, uv * 1.3 - float2(t * 0.008, -t * 0.011), LAVA).rgb;
            float pulse = 1.0 + 0.12 * sin(t * 1.4 + macro * 6.0);
            float3 color = mix(a, b, 0.5) * pulse;
            color = mix(color * 0.35, color, smoothstep(0.0, 0.12, coast));
            return float4(min(color, float3(1.0)), 1.0);
        }
        // Deep blue far out, lighter over the shallows.
        float deep = smoothstep(0.0, 2.0, out + (macro - 0.5) * 0.6);
        float3 shallow = float3(0.13, 0.55, 0.70);
        float3 open = mix(float3(0.035, 0.20, 0.52), float3(0.055, 0.26, 0.60), smoothstep(0.2, 0.8, patches));
        float3 color = mix(shallow, open, deep);
        // Ripples drifting two ways, against the water texture's own average, and
        // the sun glinting off them.
        float3 a = textures.sample(ground, uv * 1.1 + float2(t * 0.020, t * 0.013), WATER).rgb;
        float3 b = textures.sample(ground, uv * 1.7 + float2(-t * 0.017, t * 0.021), WATER).rgb;
        float3 mean = textures.sample(ground, uv, WATER, level(8.0)).rgb;
        const float3 luma = float3(0.3, 0.5, 0.2);
        float ripple = dot(mix(a, b, 0.5), luma) / max(dot(mean, luma), 0.02) - 1.0;
        color *= 1.0 + 1.3 * ripple;
        // Now and then a wave breaks out at sea.
        float crest = smoothstep(0.28, 0.45, ripple) * smoothstep(0.62, 0.75, detail) * deep;
        color = mix(color, float3(0.85, 0.92, 1.0), crest * 0.5);
        float2 tilt = float2(
            textures.sample(ground, map * 0.8 + float2(t * 0.05, 0.0), NOISE).r,
            textures.sample(ground, map * 0.8 + float2(0.0, t * 0.04) + 0.5, NOISE).r) - 0.5;
        float3 wn = normalize(float3(tilt * 0.9, 1.0));
        float glint = pow(max(dot(reflect(-u.light.xyz, wn), VIEW), 0.0), 40.0);
        color += float3(1.0, 0.97, 0.88) * glint * 0.45;
        // Foam along the shore, and lines of surf rolling in.
        float churn = textures.sample(ground, map * 1.3 + float2(t * 0.03, -t * 0.02), NOISE).r;
        float edge = 1.0 - smoothstep(0.0, 0.035 + 0.05 * churn, coast);
        float roll = sin(out * 10.0 + churn * 3.0 - t * 1.7);
        float surf = smoothstep(0.7, 0.95, roll) * (1.0 - smoothstep(0.08, 0.7, out)) * smoothstep(0.35, 0.6, churn);
        float foam = max(edge, surf * 0.75);
        color = mix(color, float3(0.95, 0.97, 1.0), foam * 0.9);
        return float4(min(color, float3(1.0)), 1.0);
    }

    // Land: the landscape's ground, with meadows where it is grass.
    float3 color = groundColor(textures, uv, landLayer) * (0.9 + 0.2 * macro);
    if (u.layers.z > 0.5) {
        float meadow = smoothstep(0.42, 0.6, patches + (detail - 0.5) * 0.3);
        color = mix(color, groundColor(textures, uv, MEADOW) * (0.9 + 0.2 * macro), meadow);
    }
    // Rock breaks through on some slopes.
    float steep = smoothstep(0.03, 0.10, 1.0 - n.z);
    float outcrop = smoothstep(0.74, 0.8, macro + (detail - 0.5) * 0.6) * steep;
    float3 rock = groundColor(textures, uv * 1.3, ROCK);
    color = mix(color, rock, outcrop);
    // The shore: rocks, with coves of sand, and wet sand at the water's edge.
    float shore = smoothstep(-0.26, -0.13, coast) * (1.0 - smoothstep(0.45, 1.0, in.place.z));
    float sandy = smoothstep(0.42, 0.56, patches + (fine - 0.5) * 0.2);
    float3 beach = mix(rock, groundColor(textures, uv, beachLayer), sandy);
    beach *= mix(1.0, 0.82, sandy * smoothstep(-0.06, 0.0, coast));
    color = mix(color, beach, shore);
    // Farmland and scorched earth, with ragged edges near their cells' own.
    color = mix(color, groundColor(textures, uv, FARMLAND), 0.92 * ragged(in.weights.y, detail));
    color = mix(color, groundColor(textures, uv, SCORCHED), ragged(in.weights.z, detail));
    return float4(min(shade(color, n, u), float3(1.0)), 1.0);
}

// Models: buildings, trees, people, placed by a per-model offset, turn and size.

struct ModelVertex {
    packed_float3 position;
    packed_float3 normal;
    packed_float3 color;
    float surface;  // 3 plain, 4 the side's colour, 5 glowing
};

struct Placement {
    float4 offset;  // xyz: base in view corners and height steps; w: turn in radians
    float4 team;    // rgb: the side's colour for team-coloured parts; w: size
};

struct ModelOut {
    float4 position [[position]];
    float3 color;
    float3 normal;
    float surface [[flat]];
};

vertex ModelOut model_vertex(
    uint id [[vertex_id]], const device ModelVertex *vertices [[buffer(0)]], constant Placement &place [[buffer(2)]]
) {
    ModelVertex v = vertices[id];
    float3 p = float3(v.position) * place.team.w;
    float3 n = float3(v.normal);
    float c = cos(place.offset.w), s = sin(place.offset.w);
    float2 turned = float2(c * p.x - s * p.y, s * p.x + c * p.y);
    ModelOut out;
    out.position = project(float3(place.offset.xy + turned, place.offset.z + p.z));
    out.normal = float3(c * n.x - s * n.y, s * n.x + c * n.y, n.z);
    out.color = v.surface > 3.5 && v.surface < 4.5 ? place.team.rgb : float3(v.color);
    out.surface = v.surface;
    return out;
}

fragment float4 model_fragment(ModelOut in [[stage_in]], constant Uniforms &u [[buffer(1)]]) {
    if (in.surface > 4.5) return float4(in.color, 1.0);  // glowing: fire, eyes
    float3 n = normalize(in.normal);
    float3 color = in.color;
    // Snow settles on whatever faces up.
    color = mix(color, float3(0.93, 0.95, 1.0), u.params.y * smoothstep(0.5, 0.8, n.z));
    return float4(min(shade(color, n, u), float3(1.0)), 1.0);
}
"""
