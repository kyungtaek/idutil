import Foundation
import RemoteFeature
import LocalFeature
import PodFeature
import BinaryFeature

enum DependencySnapshot {
    static func value() -> String {
        [RemoteFeature.value(), LocalFeature.value(), LocalFeature.resource(),
         PodFeatureValue(), "binary-\(BinaryFeatureValue())"].joined(separator: "/")
    }
}
