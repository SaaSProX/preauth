import AppKit
import Foundation
import ImageIO
import PDFKit
import Vision

struct OCRLine: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
}

struct OCRPage: Codable {
    let page: Int
    let text: String
    let lines: [OCRLine]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 5 || args.count == 6 else {
    fail("usage: ocr_pdf.swift INPUT.pdf START_PAGE END_PAGE OUTPUT.jsonl [right]")
}

let input = args[1]
guard let startPage = Int(args[2]), let endPage = Int(args[3]), startPage >= 1, endPage >= startPage else {
    fail("invalid page range")
}
let output = args[4]
let orientation: CGImagePropertyOrientation = args.count == 6 && args[5] == "right" ? .right : .up
guard let document = PDFDocument(url: URL(fileURLWithPath: input)) else {
    fail("unable to open PDF")
}
guard endPage <= document.pageCount else {
    fail("end page exceeds document page count")
}

FileManager.default.createFile(atPath: output, contents: nil)
guard let outputHandle = FileHandle(forWritingAtPath: output) else {
    fail("unable to open output")
}
defer { try? outputHandle.close() }

let encoder = JSONEncoder()

for pageNumber in startPage...endPage {
    autoreleasepool {
        guard let page = document.page(at: pageNumber - 1) else { return }
        let bounds = page.bounds(for: .mediaBox)
        let targetWidth = 1800.0
        let targetHeight = targetWidth * bounds.height / bounds.width
        let thumbnail = page.thumbnail(of: NSSize(width: targetWidth, height: targetHeight), for: .mediaBox)
        var rect = NSRect(origin: .zero, size: thumbnail.size)
        guard let cgImage = thumbnail.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { return }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["en-GB", "en-US"]
        request.usesLanguageCorrection = true

        do {
            try VNImageRequestHandler(cgImage: cgImage, orientation: orientation).perform([request])
            let observations = (request.results ?? []).sorted {
                let yDifference = $0.boundingBox.midY - $1.boundingBox.midY
                if abs(yDifference) > 0.008 { return yDifference > 0 }
                return $0.boundingBox.minX < $1.boundingBox.minX
            }
            let lines = observations.compactMap { observation -> OCRLine? in
                guard let candidate = observation.topCandidates(1).first else { return nil }
                return OCRLine(
                    text: candidate.string,
                    confidence: candidate.confidence,
                    x: observation.boundingBox.minX,
                    y: observation.boundingBox.midY
                )
            }
            let result = OCRPage(page: pageNumber, text: lines.map(\.text).joined(separator: "\n"), lines: lines)
            let encoded = try encoder.encode(result)
            outputHandle.write(encoded)
            outputHandle.write(Data("\n".utf8))
            FileHandle.standardError.write(Data(("OCR page \(pageNumber)\n").utf8))
        } catch {
            FileHandle.standardError.write(Data(("OCR failed on page \(pageNumber): \(error)\n").utf8))
        }
    }
}
