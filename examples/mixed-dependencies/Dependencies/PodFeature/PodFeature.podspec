Pod::Spec.new do |s|
  s.name = 'PodFeature'
  s.version = '1.0.0'
  s.summary = 'Local CocoaPods fixture for cache consistency experiments.'
  s.homepage = 'https://example.invalid/ios-disk-util-fixture'
  s.license = { :type => 'MIT' }
  s.author = 'iOS Disk Util fixture'
  s.source = { :git => 'https://example.invalid/fixture.git', :tag => s.version.to_s }
  s.ios.deployment_target = '17.0'
  s.osx.deployment_target = '14.0'
  s.source_files = 'Sources/*.{h,m}'
  s.public_header_files = 'Sources/*.h'
  s.static_framework = true
end
