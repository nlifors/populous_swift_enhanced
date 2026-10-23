import CoreGraphics
import Foundation
import Metal
import PopulousAssets
import PopulousRender

/// One frame for the GPU to show.
public struct DisplayFrame {
    /// The screen underneath, as 8-bit RGBA: the whole classic picture, or in HD the
    /// board without its close-up.
    public var under: [UInt8]
    public var underWidth = 320
    public var underHeight = 200
    /// 320x200 RGBA drawn over everything (dialog boxes, the pointer); pixels with
    /// zero alpha let what is below show through.
    public var over: [UInt8]?
    /// The close-up to draw in HD. Nil in classic mode, where it is in `under`.
    public var closeUp: CloseUp?
    /// The landscape, 0...3.
    public var terrain: Int
    /// Seconds since the game started, for animated water.
    public var time: Double

    public init(under: [UInt8], over: [UInt8]?, closeUp: CloseUp?, terrain: Int, time: Double) {
        self.under = under
        self.over = over
        self.closeUp = closeUp
        self.terrain = terrain
        self.time = time
    }
}

public enum ScreenRendererError: Error {
    case noGPU
    case setup(String)
}

/// Draws game frames with Metal: the classic 320x200 picture scaled up, or the
/// board with an HD close-up drawn into it.
public final class ScreenRenderer {
    public static let pixelFormat = MTLPixelFormat.bgra8Unorm
    static let sampleCount = 4
    static let depthFormat = MTLPixelFormat.depth32Float

    public let device: MTLDevice
    let queue: MTLCommandQueue
    let quadPipeline: MTLRenderPipelineState
    let terrainPipeline: MTLRenderPipelineState
    let modelPipeline: MTLRenderPipelineState
    /// Tested and written: land and models.
    let depthTested: MTLDepthStencilState
    /// Tested but not written: pictures among the land and models.
    let depthTestedOnly: MTLDepthStencilState
    let depthIgnored: MTLDepthStencilState

    /// Every model's triangles in one buffer, and where each one is in it.
    let modelBuffer: MTLBuffer?
    let modelRanges: [String: Range<Int>]

    /// The ground textures, one layer each (see `GroundTextures`).
    let groundTextures: MTLTexture?
    let tileLayout: AtlasLayout
    let tileAtlases: [MTLTexture]
    let spriteLayout: AtlasLayout
    let spriteAtlas: MTLTexture

    var underTexture: MTLTexture?
    var overTexture: MTLTexture?
    var colorTarget: MTLTexture?
    var depthTarget: MTLTexture?

    public init(
        assets: GameAssets, models: ModelLibrary = .bundled(), device: MTLDevice? = MTLCreateSystemDefaultDevice()
    ) throws {
        guard let device, let queue = device.makeCommandQueue() else { throw ScreenRendererError.noGPU }
        self.device = device
        self.queue = queue

        let library: MTLLibrary
        do {
            library = try device.makeLibrary(source: shaderSource, options: nil)
        } catch {
            throw ScreenRendererError.setup("shaders: \(error)")
        }
        func pipeline(_ vertex: String, _ fragment: String) throws -> MTLRenderPipelineState {
            let descriptor = MTLRenderPipelineDescriptor()
            descriptor.vertexFunction = library.makeFunction(name: vertex)
            descriptor.fragmentFunction = library.makeFunction(name: fragment)
            descriptor.colorAttachments[0].pixelFormat = Self.pixelFormat
            descriptor.depthAttachmentPixelFormat = Self.depthFormat
            descriptor.rasterSampleCount = Self.sampleCount
            return try device.makeRenderPipelineState(descriptor: descriptor)
        }
        quadPipeline = try pipeline("quad_vertex", "quad_fragment")
        terrainPipeline = try pipeline("land_vertex", "land_fragment")
        modelPipeline = try pipeline("model_vertex", "model_fragment")

        let tested = MTLDepthStencilDescriptor()
        tested.depthCompareFunction = .lessEqual
        tested.isDepthWriteEnabled = true
        let testedOnly = MTLDepthStencilDescriptor()
        testedOnly.depthCompareFunction = .lessEqual
        testedOnly.isDepthWriteEnabled = false
        let ignored = MTLDepthStencilDescriptor()
        ignored.depthCompareFunction = .always
        ignored.isDepthWriteEnabled = false
        guard let depthTested = device.makeDepthStencilState(descriptor: tested),
            let depthTestedOnly = device.makeDepthStencilState(descriptor: testedOnly),
            let depthIgnored = device.makeDepthStencilState(descriptor: ignored)
        else { throw ScreenRendererError.setup("depth states") }
        self.depthTested = depthTested
        self.depthTestedOnly = depthTestedOnly
        self.depthIgnored = depthIgnored

        var allModels: [CloseUpMesh.ModelVertex] = []
        var ranges: [String: Range<Int>] = [:]
        for (name, vertices) in models.models.sorted(by: { $0.key < $1.key }) {
            ranges[name] = allModels.count..<allModels.count + vertices.count
            allModels += vertices
        }
        modelRanges = ranges
        modelBuffer = allModels.isEmpty
            ? nil
            : device.makeBuffer(bytes: allModels, length: MemoryLayout<CloseUpMesh.ModelVertex>.stride * allModels.count)

        let palette = assets.gamePalette
        groundTextures = GroundTextures.load(device: device, queue: queue)

        func atlas(_ images: [IndexedImage], columns: Int) throws -> (AtlasLayout, MTLTexture) {
            let layout = AtlasLayout(
                count: images.count, columns: columns, cellWidth: images[0].width, cellHeight: images[0].height)
            let sheet = IndexedImage.sheet(images, columns: columns)
            guard let texture = Self.texture(device, sheet.rgba(palette: palette), width: sheet.width, height: sheet.height)
            else { throw ScreenRendererError.setup("atlas") }
            return (layout, texture)
        }
        var tileLayout: AtlasLayout?
        var tileAtlases: [MTLTexture] = []
        for land in assets.landSets {
            let (layout, texture) = try atlas(land.tiles, columns: 16)
            tileLayout = layout
            tileAtlases.append(texture)
        }
        guard let tileLayout else { throw ScreenRendererError.setup("no land sets") }
        self.tileLayout = tileLayout
        self.tileAtlases = tileAtlases
        (spriteLayout, spriteAtlas) = try atlas(assets.sprites, columns: 16)
    }

    static func texture(_ device: MTLDevice, _ rgba: [UInt8], width: Int, height: Int) -> MTLTexture? {
        let descriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: .rgba8Unorm, width: width, height: height, mipmapped: false)
        descriptor.usage = .shaderRead
        guard let texture = device.makeTexture(descriptor: descriptor) else { return nil }
        upload(rgba, to: texture)
        return texture
    }

    static func upload(_ rgba: [UInt8], to texture: MTLTexture) {
        rgba.withUnsafeBytes {
            texture.replace(
                region: MTLRegionMake2D(0, 0, texture.width, texture.height), mipmapLevel: 0,
                withBytes: $0.baseAddress!, bytesPerRow: texture.width * 4)
        }
    }

    /// A 320x200-style layer texture, reused while its size stays the same.
    func layer(_ rgba: [UInt8], width: Int, height: Int, reusing old: MTLTexture?) -> MTLTexture? {
        var texture = old
        if texture?.width != width || texture?.height != height {
            texture = Self.texture(device, rgba, width: width, height: height)
        } else if let texture {
            Self.upload(rgba, to: texture)
        }
        return texture
    }

    /// Multisampled colour and depth buffers matching the target's size.
    func targets(width: Int, height: Int) -> (MTLTexture, MTLTexture)? {
        if let color = colorTarget, let depth = depthTarget, color.width == width, color.height == height {
            return (color, depth)
        }
        func make(_ format: MTLPixelFormat) -> MTLTexture? {
            let descriptor = MTLTextureDescriptor.texture2DDescriptor(
                pixelFormat: format, width: width, height: height, mipmapped: false)
            descriptor.textureType = .type2DMultisample
            descriptor.sampleCount = Self.sampleCount
            descriptor.usage = .renderTarget
            descriptor.storageMode = .private
            return device.makeTexture(descriptor: descriptor)
        }
        guard let color = make(Self.pixelFormat), let depth = make(Self.depthFormat) else { return nil }
        colorTarget = color
        depthTarget = depth
        return (color, depth)
    }

    /// Records the drawing of `frame` into `target`, with the 320x200 screen
    /// stretched over `screen` (in the target's pixels) and black around it.
    public func encode(_ frame: DisplayFrame, commandBuffer: MTLCommandBuffer, target: MTLTexture, screen: CGRect) {
        guard let (color, depth) = targets(width: target.width, height: target.height) else { return }
        underTexture = layer(frame.under, width: frame.underWidth, height: frame.underHeight, reusing: underTexture)
        if let over = frame.over { overTexture = layer(over, width: 320, height: 200, reusing: overTexture) }

        let pass = MTLRenderPassDescriptor()
        pass.colorAttachments[0].texture = color
        pass.colorAttachments[0].resolveTexture = target
        pass.colorAttachments[0].loadAction = .clear
        pass.colorAttachments[0].clearColor = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 1)
        pass.colorAttachments[0].storeAction = .multisampleResolve
        pass.depthAttachment.texture = depth
        pass.depthAttachment.loadAction = .clear
        pass.depthAttachment.clearDepth = 1
        pass.depthAttachment.storeAction = .dontCare
        guard let encoder = commandBuffer.makeRenderCommandEncoder(descriptor: pass) else { return }
        encoder.setViewport(MTLViewport(
            originX: screen.minX, originY: screen.minY, width: screen.width, height: screen.height, znear: 0, zfar: 1))

        let whole = [
            CloseUpMesh.QuadVertex(x: 0, y: 0, u: 0, v: 0), CloseUpMesh.QuadVertex(x: 320, y: 0, u: 1, v: 0),
            CloseUpMesh.QuadVertex(x: 0, y: 200, u: 0, v: 1), CloseUpMesh.QuadVertex(x: 320, y: 0, u: 1, v: 0),
            CloseUpMesh.QuadVertex(x: 320, y: 200, u: 1, v: 1), CloseUpMesh.QuadVertex(x: 0, y: 200, u: 0, v: 1),
        ]
        func drawPicture(_ texture: MTLTexture?) {
            guard let texture else { return }
            encoder.setRenderPipelineState(quadPipeline)
            encoder.setDepthStencilState(depthIgnored)
            encoder.setVertexBytes(whole, length: MemoryLayout<CloseUpMesh.QuadVertex>.stride * whole.count, index: 0)
            var depth: Float = 0
            encoder.setVertexBytes(&depth, length: MemoryLayout<Float>.stride, index: 1)
            encoder.setFragmentTexture(texture, index: 0)
            encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: whole.count)
        }

        drawPicture(underTexture)
        if let closeUp = frame.closeUp { drawCloseUp(closeUp, frame: frame, encoder: encoder) }
        if frame.over != nil { drawPicture(overTexture) }
        encoder.endEncoding()
    }

    /// The close-up in HD: the land, then the models, both depth-tested so they
    /// hide one another properly, then the original's pictures for what has no
    /// model yet, hidden where land or models stand in front of them.
    func drawCloseUp(_ closeUp: CloseUp, frame: DisplayFrame, encoder: MTLRenderCommandEncoder) {
        let terrain = min(max(frame.terrain, 0), GroundTextures.landscapes.count - 1)
        let mesh = CloseUpMesh(
            closeUp, terrain: terrain, tiles: tileLayout, sprites: spriteLayout,
            available: Set(modelRanges.keys))
        func buffer<T: BitwiseCopyable>(_ items: [T]) -> MTLBuffer? {
            items.isEmpty
                ? device.makeBuffer(length: 16)
                : items.withUnsafeBytes { device.makeBuffer(bytes: $0.baseAddress!, length: $0.count) }
        }
        guard let land = buffer(mesh.terrain), let pictures = buffer(mesh.quads) else { return }

        var uniforms = Uniforms(
            light: SIMD4(simdNormalize(SIMD3(-0.55, 0.4, 0.9)), 0),
            fill: SIMD4(simdNormalize(SIMD3(1.0, 0.2, 0.5)), 0),
            params: SIMD4(
                Float(frame.time.truncatingRemainder(dividingBy: 3600)), terrain == 2 ? 0.85 : 0,
                Float(closeUp.viewX), Float(closeUp.viewY)),
            layers: SIMD4(
                Float(GroundTextures.landscapes[terrain].ground), Float(GroundTextures.landscapes[terrain].beach), terrain == 0 ? 1 : 0,
                terrain == GroundTextures.lavaLandscape ? 1 : 0))

        encoder.setRenderPipelineState(terrainPipeline)
        encoder.setDepthStencilState(depthTested)
        encoder.setVertexBuffer(land, offset: 0, index: 0)
        encoder.setFragmentBytes(&uniforms, length: MemoryLayout<Uniforms>.stride, index: 1)
        encoder.setFragmentTexture(groundTextures, index: 0)
        encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: mesh.terrain.count)

        if let modelBuffer, !mesh.models.isEmpty {
            encoder.setRenderPipelineState(modelPipeline)
            encoder.setVertexBuffer(modelBuffer, offset: 0, index: 0)
            encoder.setFragmentBytes(&uniforms, length: MemoryLayout<Uniforms>.stride, index: 1)
            for model in mesh.models {
                guard let range = modelRanges[model.model] else { continue }
                var placement = ModelPlacement(
                    offset: SIMD4(model.x, model.y, model.z, model.angle),
                    team: SIMD4(Self.teamColours[model.side ?? 0], model.scale))
                encoder.setVertexBytes(&placement, length: MemoryLayout<ModelPlacement>.stride, index: 2)
                encoder.drawPrimitives(type: .triangle, vertexStart: range.lowerBound, vertexCount: range.count)
            }
        }

        encoder.setRenderPipelineState(quadPipeline)
        encoder.setVertexBuffer(pictures, offset: 0, index: 0)
        for picture in mesh.pictures {
            var depth = picture.depth ?? 0
            encoder.setDepthStencilState(picture.depth == nil ? depthIgnored : depthTestedOnly)
            encoder.setVertexBytes(&depth, length: MemoryLayout<Float>.stride, index: 1)
            encoder.setFragmentTexture(picture.sheet == .tiles ? tileAtlases[terrain] : spriteAtlas, index: 0)
            encoder.drawPrimitives(type: .triangle, vertexStart: picture.range.lowerBound, vertexCount: picture.range.count)
        }
    }

    /// Blue for the player's side, red for the other.
    static let teamColours: [SIMD3<Float>] = [SIMD3(0.13, 0.33, 0.85), SIMD3(0.82, 0.12, 0.09)]

    /// Draws `frame` off screen and returns the picture, for screenshots.
    public func snapshot(_ frame: DisplayFrame, width: Int, height: Int, screen: CGRect) -> CGImage? {
        let descriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: Self.pixelFormat, width: width, height: height, mipmapped: false)
        descriptor.usage = .renderTarget
        descriptor.storageMode = .managed
        guard let target = device.makeTexture(descriptor: descriptor), let commands = queue.makeCommandBuffer() else {
            return nil
        }
        encode(frame, commandBuffer: commands, target: target, screen: screen)
        if let blit = commands.makeBlitCommandEncoder() {
            blit.synchronize(resource: target)
            blit.endEncoding()
        }
        commands.commit()
        commands.waitUntilCompleted()
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        target.getBytes(&bytes, bytesPerRow: width * 4, from: MTLRegionMake2D(0, 0, width, height), mipmapLevel: 0)
        guard let provider = CGDataProvider(data: Data(bytes) as CFData) else { return nil }
        return CGImage(
            width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
    }

    /// Draws `frame` into a drawable and presents it.
    public func present(_ frame: DisplayFrame, to drawable: MTLDrawable, target: MTLTexture, screen: CGRect) {
        guard let commands = queue.makeCommandBuffer() else { return }
        encode(frame, commandBuffer: commands, target: target, screen: screen)
        commands.present(drawable)
        commands.commit()
    }
}

/// Matches `Placement` in the shaders.
struct ModelPlacement {
    var offset: SIMD4<Float>
    var team: SIMD4<Float>
}

/// Matches `Uniforms` in the shaders.
struct Uniforms {
    var light: SIMD4<Float>
    var fill: SIMD4<Float>
    var params: SIMD4<Float>
    var layers: SIMD4<Float>
}
